# encoding: utf-8
#
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Author: Kyle Lahnakoski (kyle@lahnakoski.com)
#
# THIS THREADING MODULE IS PERMEATED BY THE please_stop SIGNAL.
# THIS SIGNAL IS IMPORTANT FOR PROPER SIGNALLING WHICH ALLOWS
# FOR FAST AND PREDICTABLE SHUTDOWN AND CLEANUP OF THREADS

from __future__ import absolute_import
from __future__ import division
from __future__ import unicode_literals

import signal as _signal
import sys
from copy import copy
from datetime import datetime, timedelta
from time import sleep

from mo_dots import Data, unwraplist, Null
from mo_future import get_ident, start_new_thread, interrupt_main, get_function_name, text_type, allocate_lock
from mo_logs import Log, Except
from mo_threads.lock import Lock
from mo_threads.profiles import CProfiler
from mo_threads.signal import AndSignals, Signal
from mo_threads.till import Till

DEBUG = False

MAX_DATETIME = datetime(2286, 11, 20, 17, 46, 39)
DEFAULT_WAIT_TIME = timedelta(minutes=10)
THREAD_STOP = "stop"
THREAD_TIMEOUT = "TIMEOUT"

datetime.strptime('2012-01-01', '%Y-%m-%d')  # http://bugs.python.org/issue7980


class AllThread(object):
    """
    RUN ALL ADDED FUNCTIONS IN PARALLEL, BE SURE TO HAVE JOINED BEFORE EXIT
    """

    def __init__(self):
        self.threads = []

    def __enter__(self):
        return self

    # WAIT FOR ALL QUEUED WORK TO BE DONE BEFORE RETURNING
    def __exit__(self, type, value, traceback):
        self.join()

    def join(self):
        exceptions = []
        for t in self.threads:
            try:
                t.join()
            except Exception as e:
                exceptions.append(e)

        if exceptions:
            Log.error("Problem in child threads", cause=exceptions)


    def add(self, target, *args, **kwargs):
        """
        target IS THE FUNCTION TO EXECUTE IN THE THREAD
        """
        t = Thread.run(target.__name__, target, *args, **kwargs)
        self.threads.append(t)


class BaseThread(object):
    __slots__ = ["id", "name", "children"]

    def __init__(self, ident):
        self.id = ident
        if ident != -1:
            self.name = "Unknown Thread " + text_type(ident)
        self.children = []

    def add_child(self, child):
        self.children.append(child)

    def remove_child(self, child):
        try:
            self.children.remove(child)
        except Exception:
            pass


class MainThread(BaseThread):
    def __init__(self):
        BaseThread.__init__(self, get_ident())
        self.name = "Main Thread"
        self.please_stop = Signal()
        self.stop_logging = Log.stop
        self.timers = None
        self.cprofiler = Null

    def stop(self):
        """
        BLOCKS UNTIL ALL THREADS HAVE STOPPED
        THEN RUNS sys.exit(0)
        """
        self.please_stop.go()

        self_thread = Thread.current()
        if self_thread != MAIN_THREAD or self_thread != self:
            Log.error("Only the main thread can call stop() on main thread")

        join_errors = []
        children = copy(self.children)
        for c in reversed(children):
            DEBUG and c.name and Log.note("Stopping thread {{name|quote}}", name=c.name)
            try:
                c.stop()
            except Exception as e:
                join_errors.append(e)

        for c in children:
            DEBUG and c.name and Log.note("Joining on thread {{name|quote}}", name=c.name)
            try:
                c.join()
            except Exception as e:
                join_errors.append(e)

            DEBUG and c.name and Log.note("Done join on thread {{name|quote}}", name=c.name)

        if join_errors:
            Log.error("Problem while stopping {{name|quote}}", name=self.name, cause=unwraplist(join_errors))

        self.stop_logging()
        self.timers.stop()
        self.timers.join()

        DEBUG and Log.note("Thread {{name|quote}} now stopped", name=self.name)
        sys.exit(0)

    def wait_for_shutdown_signal(
        self,
        please_stop=False,  # ASSIGN SIGNAL TO STOP EARLY
        allow_exit=False,  # ALLOW "exit" COMMAND ON CONSOLE TO ALSO STOP THE APP
        wait_forever=True  # IGNORE CHILD THREADS, NEVER EXIT.  False => IF NO CHILD THREADS LEFT, THEN EXIT
    ):
        """
        FOR USE BY PROCESSES THAT NEVER DIE UNLESS EXTERNAL SHUTDOWN IS REQUESTED

        CALLING THREAD WILL SLEEP UNTIL keyboard interrupt, OR please_stop, OR "exit"

        :param please_stop:
        :param allow_exit:
        :param wait_forever:: Assume all needed threads have been launched. When done
        :return:
        """
        self_thread = Thread.current()
        if self_thread != MAIN_THREAD or self_thread != self:
            Log.error("Only the main thread can sleep forever (waiting for KeyboardInterrupt)")

        if isinstance(please_stop, Signal):
            self.please_stop.on_go(please_stop.go)
        else:
            please_stop = self.please_stop

        if not wait_forever:
            # TRIGGER SIGNAL WHEN ALL CHILDREN THEADS ARE DONE
            pending = copy(self_thread.children)
            all = AndSignals(please_stop, len(pending))
            for p in pending:
                p.stopped.on_go(all.done)

        try:
            if allow_exit:
                _wait_for_exit(please_stop)
            else:
                _wait_for_interrupt(please_stop)
        except KeyboardInterrupt as _:
            Log.alert("SIGINT Detected!  Stopping...")
        except SystemExit as _:
            Log.alert("SIGTERM Detected!  Stopping...")
        finally:
            self.stop()


class Thread(BaseThread):
    """
    join() ENHANCED TO ALLOW CAPTURE OF CTRL-C, AND RETURN POSSIBLE THREAD EXCEPTIONS
    run() ENHANCED TO CAPTURE EXCEPTIONS
    """

    num_threads = 0

    def __init__(self, name, target, *args, **kwargs):
        BaseThread.__init__(self, -1)
        self.name = name
        self.target = target
        self.end_of_thread = None
        self.synch_lock = Lock("response synch lock")
        self.args = args

        # ENSURE THERE IS A SHARED please_stop SIGNAL
        self.kwargs = copy(kwargs)
        self.kwargs["please_stop"] = self.kwargs.get("please_stop", Signal("please_stop for " + self.name))
        self.please_stop = self.kwargs["please_stop"]

        self.thread = None
        self.stopped = Signal("stopped signal for " + self.name)
        self.cprofiler = Null

        if "parent_thread" in kwargs:
            del self.kwargs["parent_thread"]
            self.parent = kwargs["parent_thread"]
        else:
            self.parent = Thread.current()
            self.parent.add_child(self)

    def __enter__(self):
        return self

    def __exit__(self, type, value, traceback):
        if isinstance(type, BaseException):
            self.please_stop.go()

        # TODO: AFTER A WHILE START KILLING THREAD
        self.join()
        self.args = None
        self.kwargs = None

    def start(self):
        try:
            self.thread = start_new_thread(Thread._run, (self,))
            return self
        except Exception as e:
            Log.error("Can not start thread", e)

    def stop(self):
        for c in copy(self.children):
            DEBUG and c.name and Log.note("Stopping thread {{name|quote}}", name=c.name)
            c.stop()
        self.please_stop.go()

        DEBUG and Log.note("Thread {{name|quote}} got request to stop", name=self.name)

    def _run(self):
        self.id = get_ident()
        with RegisterThread(self):
            try:
                if self.target is not None:
                    a, k, self.args, self.kwargs = self.args, self.kwargs, None, None
                    self.cprofiler = CProfiler()
                    with self.cprofiler:  # PROFILE IN HERE SO THAT __exit__() IS RUN BEFORE THREAD MARKED AS stopped
                        response = self.target(*a, **k)
                    with self.synch_lock:
                        self.end_of_thread = Data(response=response)
                else:
                    with self.synch_lock:
                        self.end_of_thread = Null
            except Exception as e:
                e = Except.wrap(e)
                with self.synch_lock:
                    self.end_of_thread = Data(exception=e)
                if self not in self.parent.children:
                    # THREAD FAILURES ARE A PROBLEM ONLY IF NO ONE WILL BE JOINING WITH IT
                    try:
                        Log.fatal("Problem in thread {{name|quote}}", name=self.name, cause=e)
                    except Exception:
                        sys.stderr.write(str("ERROR in thread: " + self.name + " " + text_type(e) + "\n"))
            finally:
                try:
                    children = copy(self.children)
                    for c in children:
                        try:
                            if DEBUG:
                                sys.stdout.write(str("Stopping thread " + c.name + "\n"))
                            c.stop()
                        except Exception as e:
                            Log.warning("Problem stopping thread {{thread}}", thread=c.name, cause=e)

                    for c in children:
                        try:
                            if DEBUG:
                                sys.stdout.write(str("Joining on thread " + c.name + "\n"))
                            c.join()
                        except Exception as e:
                            Log.warning("Problem joining thread {{thread}}", thread=c.name, cause=e)
                        finally:
                            if DEBUG:
                                sys.stdout.write(str("Joined on thread " + c.name + "\n"))

                    del self.target, self.args, self.kwargs
                    DEBUG and Log.note("thread {{name|quote}} stopping", name=self.name)
                except Exception as e:
                    DEBUG and Log.warning("problem with thread {{name|quote}}", cause=e, name=self.name)
                finally:
                    self.stopped.go()
                    DEBUG and Log.note("thread {{name|quote}} is done", name=self.name)

    def is_alive(self):
        return not self.stopped

    def join(self, till=None):
        """
        RETURN THE RESULT {"response":r, "exception":e} OF THE THREAD EXECUTION (INCLUDING EXCEPTION, IF EXISTS)
        """
        if self is Thread:
            Log.error("Thread.join() is not a valid call, use t.join()")

        children = copy(self.children)
        for c in children:
            c.join(till=till)

        DEBUG and Log.note("{{parent|quote}} waiting on thread {{child|quote}}", parent=Thread.current().name, child=self.name)
        (self.stopped | till).wait()
        if self.stopped:
            self.parent.remove_child(self)
            if not self.end_of_thread.exception:
                return self.end_of_thread.response
            else:
                Log.error("Thread {{name|quote}} did not end well", name=self.name, cause=self.end_of_thread.exception)
        else:
            raise Except(type=THREAD_TIMEOUT)

    @staticmethod
    def run(name, target, *args, **kwargs):
        # ENSURE target HAS please_stop ARGUMENT
        if get_function_name(target) == 'wrapper':
            pass  # GIVE THE override DECORATOR A PASS
        elif "please_stop" not in target.__code__.co_varnames:
            Log.error("function must have please_stop argument for signalling emergency shutdown")

        Thread.num_threads += 1

        output = Thread(name, target, *args, **kwargs)
        output.start()
        return output

    @staticmethod
    def current():
        ident = get_ident()
        with ALL_LOCK:
            output = ALL.get(ident)

        if output is None:
            output = BaseThread(ident)
            with ALL_LOCK:
                ALL[ident] = output

        return output


class RegisterThread(object):

    def __init__(self, thread):
        self.thread = thread

    def __enter__(self):
        with ALL_LOCK:
            ALL[self.thread.id] = self.thread
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        with ALL_LOCK:
            del ALL[self.thread.id]


def stop_main_thread(*args):
    global DEBUG

    DEBUG = True
    try:
        if len(args):
            Log.warning("exit with {{value}}", value=_describe_exit_codes.get(args[0], args[0]))
    except Exception as _:
        pass
    finally:
        MAIN_THREAD.stop()


_describe_exit_codes = {
    _signal.SIGTERM: "SIGTERM",
    _signal.SIGINT: "SIGINT"
}

_signal.signal(_signal.SIGTERM, stop_main_thread)
_signal.signal(_signal.SIGINT, stop_main_thread)


def _wait_for_exit(please_stop):
    """
    /dev/null SPEWS INFINITE LINES, DO NOT POLL AS OFTEN
    """
    cr_count = 0  # COUNT NUMBER OF BLANK LINES

    please_stop.on_go(_interrupt_main_safely)

    while not please_stop:
        # if DEBUG:
        #     Log.note("inside wait-for-shutdown loop")
        if cr_count > 30:
            (Till(seconds=3) | please_stop).wait()
        try:
            line = sys.stdin.readline()
        except Exception as e:
            Except.wrap(e)
            if "Bad file descriptor" in e:
                _wait_for_interrupt(please_stop)
                break

        # if DEBUG:
        #     Log.note("read line {{line|quote}}, count={{count}}", line=line, count=cr_count)
        if line == "":
            cr_count += 1
        else:
            cr_count = -1000000  # NOT /dev/null

        if line.strip() == "exit":
            Log.alert("'exit' Detected!  Stopping...")
            return


def _wait_for_interrupt(please_stop):
    DEBUG and Log.note("inside wait-for-shutdown loop")
    while not please_stop:
        try:
            sleep(1)
        except Exception:
            pass


def _interrupt_main_safely():
    try:
        interrupt_main()
    except KeyboardInterrupt:
        # WE COULD BE INTERRUPTING SELF
        pass


MAIN_THREAD = MainThread()

ALL_LOCK = allocate_lock()
ALL = dict()
ALL[get_ident()] = MAIN_THREAD

