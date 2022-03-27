# encoding: utf-8
#
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Contact: Kyle Lahnakoski (kyle@lahnakoski.com)
#
# THIS THREADING MODULE IS PERMEATED BY THE please_stop SIGNAL.
# THIS SIGNAL IS IMPORTANT FOR PROPER SIGNALLING WHICH ALLOWS
# FOR FAST AND PREDICTABLE SHUTDOWN AND CLEANUP OF THREADS

from __future__ import absolute_import, division, unicode_literals

from weakref import ref

from mo_dots import is_null
from mo_future import allocate_lock as _allocate_lock
from mo_logs import Log, Except
from mo_logs.exceptions import get_stacktrace

DEBUG = False
TRACE_THEN = False  # GRAB STACK TRACE OF then() CALL FOR BLAME


def standard_warning(cause):
    Log.warning(
        "Trigger on Signal.go() failed, and no error function provided!",
        cause=cause,
        stack_depth=1,
    )


def debug_warning(stacktrace):
    def warning(cause):
        Log.warning(
            "Trigger on Signal.go() failed, and no error function provided!",
            cause=[cause, Except(template="attached at", trace=stacktrace)],
            stack_depth=1,
        )

    return warning


class Signal(object):
    """
    SINGLE-USE THREAD SAFE SIGNAL (aka EVENT)

    go() - ACTIVATE SIGNAL (DOES NOTHING IF SIGNAL IS ALREADY ACTIVATED)
    wait() - PUT THREAD IN WAIT STATE UNTIL SIGNAL IS ACTIVATED
    then() - METHOD FOR OTHER THREAD TO RUN WHEN ACTIVATING SIGNAL
    """

    __slots__ = ["_name", "lock", "_go", "job_queue", "waiting_threads", "__weakref__"]

    def __init__(self, name=None):
        DEBUG and name and Log.note("New signal {{name|quote}}", name=name)
        self._name = name
        self.lock = _allocate_lock()
        self._go = False
        self.job_queue = None
        self.waiting_threads = None

    def __bool__(self):
        return self._go

    def __nonzero__(self):
        return self._go

    def wait(self):
        """
        PUT THREAD IN WAIT STATE UNTIL SIGNAL IS ACTIVATED
        """
        if self._go:
            return True

        with self.lock:
            if self._go:
                return True
            stopper = _allocate_lock()
            stopper.acquire()
            if not self.waiting_threads:
                self.waiting_threads = [stopper]
            else:
                self.waiting_threads.append(stopper)

        DEBUG and self._name and Log.note("wait for go {{name|quote}}", name=self.name)
        stopper.acquire()
        DEBUG and self._name and Log.note("GOing! {{name|quote}}", name=self.name)
        return True

    def go(self):
        """
        ACTIVATE SIGNAL (DOES NOTHING IF SIGNAL IS ALREADY ACTIVATED)
        """
        DEBUG and self._name and Log.note("GO! {{name|quote}}", name=self.name)

        if self._go:
            return self

        with self.lock:
            if self._go:
                return self
            self._go = True

        DEBUG and self._name and Log.note("internal GO! {{name|quote}}", name=self.name)
        jobs, self.job_queue = self.job_queue, None
        threads, self.waiting_threads = self.waiting_threads, None

        if threads:
            DEBUG and self._name and Log.note(
                "Release {{num}} threads", num=len(threads)
            )
            for t in threads:
                t.release()

        if jobs:
            for j, e in jobs:
                try:
                    j()
                except Exception as cause:
                    e(cause)
        return self

    def then(self, target, error=standard_warning):
        """
        RUN target WHEN SIGNALED
        """
        if DEBUG:
            if not target:
                Log.error("expecting target")
            if isinstance(target, Signal):
                Log.error("expecting a function, not a signal")

        with self.lock:
            if not self._go:
                if TRACE_THEN:
                    error = debug_warning(get_stacktrace(1))

                if not self.job_queue:
                    self.job_queue = [(target, error)]
                else:
                    self.job_queue.append((target, error))
                return self

        try:
            target()
        except Exception as cause:
            error(cause)
        return self

    def remove_then(self, target):
        """
        FOR SAVING MEMORY
        """
        if self._go:
            return

        with self.lock:
            if not self._go and self.job_queue:
                for i, (j, e) in enumerate(self.job_queue):
                    if j == target:
                        del self.job_queue[i]
                        break

    @property
    def name(self):
        if not self._name:
            return "anonymous signal"
        else:
            return self._name

    def __str__(self):
        return f"{self._go} ({self.name})"

    def __repr__(self):
        return repr(self._go)

    def __or__(self, other):
        if is_null(other):
            return self
        if not isinstance(other, Signal):
            Log.error("Expecting OR with other signal")
        if self or other:
            return DONE

        return or_signal(self, other)

    def __ror__(self, other):
        return self.__or__(other)

    def __and__(self, other):
        if is_null(other) or other:
            return self
        if not isinstance(other, Signal):
            Log.error("Expecting OR with other signal")

        if DEBUG and self._name:
            output = Signal(self.name + " & " + other.name)
        else:
            output = Signal(self.name + " & " + other.name)

        gen = AndSignals(output, 2)
        self.then(gen.done)
        other.then(gen.done)
        return output


class AndSignals(object):
    __slots__ = ["signal", "remaining", "locker"]

    def __init__(self, signal, count):
        """
        CALL signal.go() WHEN done() IS CALLED count TIMES
        :param signal:
        :param count:
        :return:
        """
        self.signal = signal
        self.locker = _allocate_lock()
        self.remaining = count
        if not count:
            self.signal.go()

    def done(self):
        with self.locker:
            self.remaining -= 1
            remaining = self.remaining
        if not remaining:
            self.signal.go()


def or_signal(*dependencies):
    output = Signal(" | ".join(d.name for d in dependencies))
    OrSignal(output, dependencies)
    return output


class OrSignal(object):
    """
    A SELF-REFERENTIAL CLUSTER OF SIGNALING METHODS TO IMPLEMENT __or__()
    MANAGE SELF-REMOVAL UPON NOT NEEDING THE signal OBJECT ANY LONGER
    """

    __slots__ = ["signal", "dependencies"]

    def __init__(self, signal, dependencies):
        self.dependencies = dependencies
        self.signal = ref(signal, self.cleanup)
        for d in dependencies:
            d.then(self)
        signal.then(self.cleanup)

    def cleanup(self, _=None):
        self.dependencies, dependencies = [], self.dependencies
        for d in dependencies:
            d.remove_then(self)

    def __call__(self):
        signal = self.signal()
        if signal is not None:
            signal.go()

    def __hash__(self):
        return id(self)

    def __eq__(self, other):
        return id(self) == id(other)


DONE = Signal().go()
