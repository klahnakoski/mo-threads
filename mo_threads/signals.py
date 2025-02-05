# encoding: utf-8
#
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at https://www.mozilla.org/en-US/MPL/2.0/.
#
# Contact: Kyle Lahnakoski (kyle@lahnakoski.com)
#
# THIS THREADING MODULE IS PERMEATED BY THE please_stop SIGNAL.
# THIS SIGNAL IS IMPORTANT FOR PROPER SIGNALLING WHICH ALLOWS
# FOR FAST AND PREDICTABLE SHUTDOWN AND CLEANUP OF THREADS


from weakref import ref

from mo_dots import is_null
from mo_future import allocate_lock as _allocate_lock
from mo_imports import expect
from mo_logs import logger, Except
from mo_logs.exceptions import get_stacktrace
from mo_logs.strings import quote

current_thread, threads = expect("current_thread", "threads")


DEBUG = False
TRACE_THEN = False  # GRAB STACK TRACE OF then() CALL FOR BLAME
MAX_NAME_LENGTH = 100


def standard_warning(cause):
    logger.warning(
        "Trigger on Signal.go() failed, and no error function provided!", cause=cause, stack_depth=1,
    )


def debug_warning(stacktrace):
    def warning(cause):
        logger.warning(
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

    __slots__ = ["_name", "lock", "_go", "job_queue", "waiting_threads", "triggered_by", "__weakref__"]

    def __init__(self, name=None):
        DEBUG and name and print(f"New signal {quote(name)}")
        self._name = name
        self.lock = _allocate_lock()
        self._go = False
        self.job_queue = None
        self.waiting_threads = None
        self.triggered_by = None

    def __bool__(self):
        return self._go

    def __nonzero__(self):
        return self._go

    def wait(self, till=None):
        """
        PUT THREAD IN WAIT STATE UNTIL SIGNAL IS ACTIVATED
        """
        if till is not None:
            # a.wait(till=b) IS AN ALTERNATE FORM FOR (a | b).wait()
            (self | till).wait()
            return True

        if self._go:
            return True

        thread = None
        if DEBUG:
            thread = current_thread()
            if threads.MAIN_THREAD.timers is thread:
                logger.error("Deadlock detected", stack_depth=1)

        with self.lock:
            if self._go:
                return True
            stopper = _allocate_lock()
            stopper.acquire()
            if not self.waiting_threads:
                self.waiting_threads = [(stopper, thread)]
            else:
                self.waiting_threads.append((stopper, thread))

        DEBUG and self._name and print(f"{quote(thread.name)} wait on {quote(self.name)}")
        stopper.acquire()
        DEBUG and self._name and print(f"{quote(thread.name)} released on {quote(self.name)}")
        return True

    def go(self):
        """
        ACTIVATE SIGNAL (DOES NOTHING IF SIGNAL IS ALREADY ACTIVATED)
        """
        DEBUG and self._name and print(f"requesting GO! {quote(self.name)}")

        if self._go:
            return self

        with self.lock:
            if self._go:
                return self
            if DEBUG:
                self.triggered_by = get_stacktrace(1)
            self._go = True

        DEBUG and self._name and print(f"GO! {quote(self.name)}")
        jobs, self.job_queue = self.job_queue, None
        stoppers, self.waiting_threads = self.waiting_threads, None

        if stoppers:
            if DEBUG:
                if len(stoppers) == 1:
                    s, t = stoppers[0]
                    print(f"Releasing thread {quote(t.name)}")
                else:
                    print(f"Release {len(stoppers)} threads")
            for s, t in stoppers:
                s.release()

        if jobs:
            for j, e in jobs:
                try:
                    j()
                except Exception as cause:
                    e(cause)
        return self

    def then(self, target, error=standard_warning):
        """
        WARNING: THIS IS RUN BY THE timer THREAD, KEEP THIS FUNCTION SHORT, AND DO NOT BLOCK
        RUN target WHEN SIGNALED
        """
        if DEBUG:
            if not target:
                logger.error("expecting target")
            if isinstance(target, Signal):
                logger.error("expecting a function, not a signal")

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
        if other is False:
            return self
        if other is True:
            return DONE
        if not isinstance(other, Signal):
            logger.error("Expecting OR with other signal")
        if self or other:
            return DONE

        return or_signal(self, other)

    __ror__ = __or__

    def __and__(self, other):
        if is_null(other):
            return self
        if other is False:
            return NEVER
        if other is True:
            return self
        if not isinstance(other, Signal):
            logger.error("Expecting OR with other signal")

        name = f"{self.name} & {other.name}"
        if len(name) > MAX_NAME_LENGTH:
            name = name[:MAX_NAME_LENGTH] + "..."
        output = Signal(name)

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
    if len(dependencies) > 5:
        name = f"{dependencies[0].name} | ({len(dependencies)} other signals)"
    else:
        name = " | ".join(d.name for d in dependencies)
    if len(name) > MAX_NAME_LENGTH:
        name = name[:MAX_NAME_LENGTH] + "..."

    output = Signal(name)
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


class Never(Signal):
    def go(self):
        return self


DONE = Signal().go()
NEVER = Never()
