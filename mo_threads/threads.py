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


import signal as _signal
import sys
import threading
from collections import namedtuple
from datetime import datetime, timedelta
from time import sleep

from mo_dots import unwraplist, Null
from mo_future import (
    allocate_lock,
    get_function_name,
    get_ident,
    start_new_thread,
    decorate,
)
from mo_imports import export
from mo_logs import Except, logger, raise_from_none
from mo_logs.exceptions import ERROR

from mo_threads.signals import AndSignals, Signal
from mo_threads.till import Till, TIMERS_NAME

DEBUG = False

PLEASE_STOP = "please_stop"  # REQUIRED thread PARAMETER TO SIGNAL STOP
PARENT_THREAD = "parent_thread"  # OPTIONAL PARAMETER TO ASSIGN THREAD TO SOMETHING OTHER THAN CURRENT THREAD
MAX_DATETIME = datetime(2286, 11, 20, 17, 46, 39)
DEFAULT_WAIT_TIME = timedelta(minutes=10)
THREAD_STOP = "stop"
THREAD_TIMEOUT = "TIMEOUT"
COVERAGE_COLLECTOR = None  # Detect Coverage.py

datetime.strptime("2012-01-01", "%Y-%m-%d")  # http://bugs.python.org/issue7980

cprofiler_stats = None  # ACCUMULATION OF STATS FROM ALL THREADS

try:
    STDOUT = sys.stdout.buffer
    STDERR = sys.stderr.buffer
    STDIN = sys.stdin.buffer
except Exception:
    STDOUT = sys.stdout
    STDERR = sys.stderr
    STDIN = sys.stdin

if sys.version_info[1] > 9:

    def is_daemon(thread):
        return thread.daemon


else:

    def is_daemon(thread):
        return thread.isDaemon()


class BaseThread(object):
    __slots__ = [
        "_ident",
        "name",
        "child_locker",
        "children",
        "parent_thread",
        "cprofiler",
        "trace_func",
        "additional_info",
    ]

    def __init__(self, ident, name):
        self._ident = ident
        self.name = name or f"Unknown Thread {ident or ''}"
        self.child_locker = allocate_lock()
        self.children = []
        self.parent_thread = None
        self.cprofiler = None
        self.trace_func = sys.gettrace()
        self.additional_info = None  # FOR DEBUGGING

    @property
    def id(self):
        return self._ident

    @property
    def ident(self):
        return self._ident

    @property
    def _is_stopped(self):
        return bool(self.stopped)

    def add_child(self, child):
        if DEBUG:
            if child.name == TIMERS_NAME:
                logger.error("timer thread should not be added as child")
            logger.info("adding child {child} to {parent}", child=child.name, parent=self.name)

        with self.child_locker:
            self.children.append(child)

    def remove_child(self, child):
        try:
            with self.child_locker:
                self.children.remove(child)
        except Exception:
            pass

    def stop(self):
        pass

    def join(self, till=None):
        DEBUG and logger.info("Joining on thread {name|quote}", name=self.name)
        with threading._active_limbo_lock:
            thread = threading._active.get(self._ident)
        if not thread:
            raise Exception("Thread has no join method")
        try:
            if not is_daemon(thread):
                while not till and thread.is_alive():
                    thread.join(1.0)
                    sys.stderr.write(f"waiting for {thread.name}")
        except Exception as cause:
            logger.error(thread.name, cause=cause)
        finally:
            with ALL_LOCK:
                del ALL[self._ident]
            with threading._active_limbo_lock:
                del threading._active[self._ident]
            try:
                self.parent.remove_child(self)
            except Exception as cause:
                logger.warning("parents of children must have remove_child() method", cause=cause)


class MainThread(BaseThread):
    def __init__(self):
        BaseThread.__init__(self, get_ident(), "Main Thread")
        self.please_stop = Signal()
        self.stopped = Signal()
        self.stop_logging = logger.stop
        self.timers = None
        self.shutdown_locker = allocate_lock()

    def stop(self):
        """
        BLOCKS UNTIL ALL KNOWN THREADS, EXCEPT MainThread, HAVE STOPPED
        """
        self_thread = Thread.current()
        if self_thread != MAIN_THREAD:
            logger.error("Only the main thread can call stop()")
        if self_thread != self:
            logger.error("Only the current thread can call stop()")

        DEBUG and logger.info("Stopping MainThread")
        if self.stopped:
            return

        self.please_stop.go()

        with self.child_locker:
            children = list(self.children)
        for c in reversed(children):
            DEBUG and c.name and logger.info("Stopping thread {name|quote}", name=c.name)
            c.stop()

        join_errors = None
        try:
            join_all_threads(children)
        except Exception as cause:
            join_errors = cause
            # REPORT ERRORS BEFORE LOGGING SHUTDOWN
            logger.warning("Problem while stopping {name|quote}", name=self.name, cause=cause, log_context=ERROR)

        with self.shutdown_locker:
            if self.stopped:
                return
            self.stop_logging()
            self.timers.stop().join()

            if cprofiler_stats is not None:
                from mo_threads.profiles import write_profiles

                write_profiles(self.cprofiler)
            DEBUG and logger.info("Thread {name|quote} now stopped", name=self.name)
            self.stopped.go()

        with ALL_LOCK:
            del ALL[self._ident]
            if ALL:
                sys.stderr.write("Expecting no further threads")

        if join_errors:
            raise Except(
                context=ERROR,
                template="Problem while stopping {name|quote}",
                name=self.name,
                cause=unwraplist(join_errors),
            )

        return self


EndOfThread = namedtuple("EndOfThread", ["response", "exception"])


class Thread(BaseThread):
    """
    join() ENHANCED TO ALLOW CAPTURE OF CTRL-C, AND RETURN POSSIBLE THREAD EXCEPTIONS
    run() ENHANCED TO CAPTURE EXCEPTIONS
    """

    def __init__(self, name, target, *args, parent_thread=None, **kwargs):
        BaseThread.__init__(self, 0, name or f"thread_{object.__hash__(self)}")
        self.target = target
        self.end_of_thread = None
        self.args = args

        # ENSURE THERE IS A SHARED please_stop SIGNAL
        self.kwargs = dict(kwargs)
        if PLEASE_STOP in self.kwargs:
            self.please_stop = self.kwargs[PLEASE_STOP]
        else:
            self.please_stop = self.kwargs[PLEASE_STOP] = Signal(f"please_stop for {self.name}")
        self.thread = None
        self.joiner_is_waiting = Signal(f"joining with {self.name}")
        self.stopped = Signal(f"stopped signal for {self.name}")

        if parent_thread is not None:  # IMPORTANT, USE Null FOR ORPHANS
            self.parent = parent_thread
        else:
            self.parent = current_thread()
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
            if self.thread:
                return
            self.thread = start_new_thread(Thread._run, (self,))
            return self
        except Exception as cause:
            logger.error("Can not start thread", cause)

    def stop(self):
        """
        SEND STOP SIGNAL, DO NOT BLOCK
        """
        with self.child_locker:
            children = list(self.children)
        for c in children:
            DEBUG and c.name and logger.note("Stopping thread {name|quote}", name=c.name)
            c.stop()
        self.please_stop.go()

        DEBUG and logger.note("Thread {name|quote} got request to stop", name=self.name)
        return self

    def is_alive(self):
        return not self.stopped

    def _run(self):
        self._ident = get_ident()
        with RegisterThread(self):
            try:
                try:
                    if self.target is not None:
                        a, k, self.args, self.kwargs = self.args, self.kwargs, None, None
                        self.end_of_thread = EndOfThread(self.target(*a, **k), None)
                except Exception as cause:
                    cause = Except.wrap(cause)
                    self.end_of_thread = EndOfThread(None, cause)
                    with self.parent.child_locker:
                        emit_problem = self not in self.parent.children
                    if emit_problem:
                        # THREAD FAILURES ARE A PROBLEM ONLY IF NO ONE WILL BE JOINING WITH IT
                        try:
                            logger.error("Problem in thread {name|quote}", name=self.name, cause=cause)
                        except Exception as cause:
                            sys.stderr.write(f"ERROR in thread: {self.name}  {cause}\n")
                finally:
                    try:
                        with self.child_locker:
                            children = list(self.children)
                        for c in children:
                            DEBUG and logger.note(f"Stopping thread {c.name}\n")
                            c.stop()

                        join_all_threads(children)
                        self.children = []
                        del self.target, self.args, self.kwargs
                        DEBUG and logger.note("thread {name|quote} stopping", name=self.name)
                    except Exception as cause:
                        DEBUG and logger.warning("problem with thread {name|quote}", cause=cause, name=self.name)
                    finally:
                        self.stopped.go()
            finally:
                if not self.joiner_is_waiting:
                    DEBUG and logger.note("thread {name|quote} is done, wait for join", name=self.name)
                    # WHERE DO WE PUT THE THREAD RESULT?
                    # IF NO THREAD JOINS WITH THIS, THEN WHAT DO WE DO WITH THE RESULT?
                    # HOW LONG DO WE WAIT FOR ANOTHER TO ACCEPT THE RESULT?
                    #
                    # WAIT 60seconds, THEN SEND RESULT TO LOGGER
                    (Till(seconds=60) | self.joiner_is_waiting).wait()
                if self.joiner_is_waiting:
                    return

                eot = self.end_of_thread
                exp = eot.exception
                if exp:
                    # THREAD FAILURES ARE A PROBLEM ONLY IF NO ONE WILL BE JOINING WITH IT
                    try:
                        logger.error(
                            "Problem in thread {name|quote}", name=self.name, cause=self.end_of_thread.exception,
                        )
                    except Exception as cause:
                        sys.stderr.write(f"ERROR in thread: {self.name} {cause}\n")
                        return

                res = eot.response
                if res is not None:
                    logger.warning(
                        "Thread {thread} returned a response {response|json}, but was not joined",
                        thread=self.name,
                        response=res,
                    )
                # THREAD ENDS OK; FORGET ABOUT IT
                if isinstance(self.parent, Thread):
                    # SOMETIMES parent IS NOT A THREAD
                    self.parent.remove_child(self)

    def release(self):
        """
        RELEASE THREAD TO FEND FOR ITSELF. THREAD CAN EXPECT TO NEVER
        JOIN. WILL SEND RESULTS TO LOGS WHEN DONE.

        PARENT THREAD WILL STILL ENSURE self HAS STOPPED PROPERLY
        """
        self.joiner_is_waiting.go()
        return self

    def join(self, till=None):
        """
        RETURN THE RESULT {"response":r, "exception":e} OF THE THREAD EXECUTION (INCLUDING EXCEPTION, IF EXISTS)
        """
        if self is Thread:
            logger.error("Thread.join() is not a valid call, use t.join()")

        if isinstance(till, (int, float)):
            till = Till(seconds=till)

        with self.child_locker:
            children = list(self.children)
        join_all_threads(children, till=till)

        DEBUG and logger.note(
            "{parent|quote} waiting on thread {child|quote}", parent=Thread.current().name, child=self.name,
        )
        self.joiner_is_waiting.go()
        (self.stopped | till).wait()
        if not self.stopped:
            raise Except(context=THREAD_TIMEOUT)

        try:
            self.parent.remove_child(self)
        except Exception as cause:
            logger.warning("parents of children must have remove_child() method", cause=cause)

        if self.end_of_thread.exception:
            logger.error(
                "Thread {name|quote} did not end well", name=self.name, cause=self.end_of_thread.exception,
            )
        return self.end_of_thread.response

    @staticmethod
    def run(name, target, *args, **kwargs):
        # ENSURE target HAS please_stop ARGUMENT
        if get_function_name(target) == "wrapper":
            pass  # GIVE THE override DECORATOR A PASS
        elif PLEASE_STOP not in target.__code__.co_varnames:
            logger.error("function must have please_stop argument for signalling emergency shutdown")

        return Thread(name, target, *args, **kwargs).start()

    @staticmethod
    def current():
        return current_thread()

    @staticmethod
    def join_all(threads):
        join_all_threads(threads)

    ####################################################################################################################
    ## threading.Thread METHODS
    ####################################################################################################################
    def is_alive(self):
        return not self.stopped

    @property
    def _is_stopped(self):
        return self.stopped

    @property
    def daemon(self):
        return False

    def isDaemon(self):
        return False

    def getName(self):
        return self.name

    def setName(self, name):
        self.name = name


class RegisterThread(object):
    """
    A context manager to handle threads spawned by other libs
    This will ensure the thread has unregistered, or
    has completed before MAIN_THREAD is shutdown
    """

    __slots__ = ["thread"]

    def __init__(self, thread=None, name=None):
        self.thread = thread or BaseThread(get_ident(), name)

    def __enter__(self):
        thread = self.thread
        ident = thread._ident
        with ALL_LOCK:
            ALL[ident] = thread
            if DEBUG:
                ALL_THREAD.append(thread)
        with threading._active_limbo_lock:
            threading._active[ident] = thread
        if cprofiler_stats is not None:
            from mo_threads.profiles import CProfiler

            cprofiler = thread.cprofiler = CProfiler()
            cprofiler.__enter__()
        if COVERAGE_COLLECTOR is not None:
            # STARTING TRACER WILL sys.settrace() ITSELF
            COVERAGE_COLLECTOR._collectors[-1]._start_tracer()

        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        # PYTHON WILL REMOVE GLOBAL VAR BEFORE END-OF-THREAD
        all_lock = ALL_LOCK
        all = ALL
        thread = self.thread
        ident = thread._ident

        if cprofiler_stats is not None:
            thread.cprofiler.__exit__(exc_type, exc_val, exc_tb)
        with thread.child_locker:
            if thread.children:
                logger.error(
                    "Thread {thread|quote} has not joined with child threads {children|json}",
                    children=[c.name for c in thread.children],
                    thread=thread.name,
                )
        with all_lock:
            del all[ident]
        with threading._active_limbo_lock:
            del threading._active[ident]


def register_thread(func):
    """
    Call `with RegisterThread():`
    Track this thread to ensure controlled shutdown
    """

    @decorate(func)
    def output(*args, **kwargs):
        with RegisterThread():
            return func(*args, **kwargs)

    return output


def _wait_for_exit(please_stop):
    """
    /dev/null PIPED TO sys.stdin SPEWS INFINITE LINES, DO NOT POLL AS OFTEN
    """
    cr_count = 0  # COUNT NUMBER OF BLANK LINES

    try:
        while not please_stop:
            # DEBUG and logger.note("inside wait-for-shutdown loop")
            if cr_count > 30:
                (Till(seconds=3) | please_stop).wait()
            try:
                # line = ""
                line = STDIN.readline()
            except Exception as cause:
                Except.wrap(cause)
                if "Bad file descriptor" in cause:
                    logger.note("can not read from stdin")
                    _wait_for_interrupt(please_stop)
                    break

            # DEBUG and logger.note("read line {line|quote}, count={count}", line=line, count=cr_count)
            if not line:
                cr_count += 1
            else:
                cr_count = -1000000  # NOT /dev/null

            if line.strip() == b"exit":
                logger.alert("'exit' Detected!  Stopping...")
                return
    except Exception as cause:
        logger.warning("programming error", cause=cause)
    finally:
        if please_stop:
            logger.note("please_stop has been requested")
        logger.note("done waiting for exit")


def current_thread():
    ident = get_ident()
    all_lock = ALL_LOCK
    all = ALL
    main_thread = MAIN_THREAD

    with all_lock:
        output = all.get(ident)

    if output is None:
        threading_thread = threading.current_thread()
        thread = BaseThread(ident, threading_thread.name)
        if cprofiler_stats is not None:
            from mo_threads.profiles import CProfiler

            thread.cprofiler = CProfiler()
            thread.cprofiler.__enter__()
        main_thread.add_child(thread)
        with all_lock:
            all[ident] = thread

        logger.warning("this thread is not known. Register this thread at earliest known entry point.")
        return thread
    return output


def join_all_threads(threads, till=None):
    """
    Join all threads, and raise any exceptions
    :param threads: list of threads to join
    :param till: signal to stop waiting for threads
    """
    causes = []
    for c in threads:
        try:
            DEBUG and logger.note(f"Joining on thread {c.name}\n")
            c.join(till=till)
        except Exception as cause:
            causes.append(cause)
        finally:
            DEBUG and logger.note(f"Joined on thread {c.name}\n")
    if causes:
        logger.error("At least one thread failed", cause=causes)


export("mo_threads.signals", current_thread)


def _wait_for_interrupt(please_stop):
    DEBUG and logger.note("wait for stop signal")
    try:
        # ALTERNATE BETWEEN please_stop CHECK AND SIGINT CHECK
        while not please_stop:
            sleep(1)  # LOCKS CAN NOT BE INTERRUPTED, ONLY sleep() CAN
    finally:
        please_stop.go()


def wait_for_shutdown_signal(
    please_stop=False,  # ASSIGN SIGNAL TO STOP EARLY
    allow_exit=False,  # ALLOW "exit" COMMAND ON CONSOLE TO ALSO STOP THE APP
    wait_forever=True,  # IGNORE CHILD THREADS, NEVER EXIT.  False => IF NO CHILD THREADS LEFT, THEN EXIT
):
    """
    FOR USE BY PROCESSES THAT NEVER DIE UNLESS EXTERNAL SHUTDOWN IS REQUESTED

    CALLING THREAD WILL SLEEP UNTIL keyboard interrupt, OR please_stop, OR "exit"
    """
    main = Thread.current()
    if main != MAIN_THREAD:
        logger.error("Only the main thread can sleep forever (waiting for KeyboardInterrupt)")

    if isinstance(please_stop, Signal):
        # MUTUAL SIGNALING MAKES THESE TWO EFFECTIVELY THE SAME SIGNAL
        main.please_stop.then(please_stop.go, raise_from_none)
        please_stop.then(main.please_stop.go)

    if not wait_forever:
        # TRIGGER SIGNAL WHEN ALL CHILDREN THREADS ARE DONE
        with main.child_locker:
            pending = list(main.children)
        DEBUG and logger.note("waiting for {children} child threads to finish", children=[c.name for c in pending])
        children_done = AndSignals(main.please_stop, len(pending))
        children_done.signal.then(main.please_stop.go)
        for p in pending:
            p.stopped.then(children_done.done)

    try:
        if allow_exit:
            _wait_for_exit(main.please_stop)
        else:
            _wait_for_interrupt(main.please_stop)
        logger.alert("Stop requested!  Stopping...")
    except KeyboardInterrupt as _:
        logger.alert("SIGINT Detected!  Stopping...")
    except SystemExit as _:
        logger.alert("SIGTERM Detected!  Stopping...")
    finally:
        main.stop()


def stop_main_thread(signum=0, frame=None):
    with ALL_LOCK:
        if not ALL:
            logger.note("All threads have shutdown")
            return

    if Thread.current() == MAIN_THREAD:
        MAIN_THREAD.stop()
    else:
        MAIN_THREAD.please_stop.go()


def start_main_thread():
    global MAIN_THREAD

    MAIN_THREAD = MainThread()

    with ALL_LOCK:
        if ALL:
            names = [f"{t.name} ({t.ident})" for k, t in ALL.items()]
            if DEBUG:
                from mo_files import File
                from mo_json import value2json

                File("all_thread.json").write(value2json([(t.name, t.id) for t in ALL_THREAD], pretty=True))
            raise Exception(f"expecting no threads {names}")

        ALL[get_ident()] = MAIN_THREAD

    # STARTUP TIMERS
    from mo_threads import till

    till.enabled = Signal()
    MAIN_THREAD.timers = Thread.run(TIMERS_NAME, till.daemon, parent_thread=Null)
    till.enabled.wait()


_signal.signal(_signal.SIGTERM, stop_main_thread)
_signal.signal(_signal.SIGINT, stop_main_thread)

MAIN_THREAD = None
ALL_LOCK = allocate_lock()
ALL = dict()
ALL_THREAD = []  # FOR DEBUGGING ONLY
