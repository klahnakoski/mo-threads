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
from mo_imports import export

from mo_threads import threads
from mo_threads.commands import Command
from mo_threads.futures import Future
from mo_threads.lock import Lock
from mo_threads.pools import ThreadPool
from mo_threads.processes import Process
from mo_threads.queues import Queue, ThreadedQueue
from mo_threads.signals import Signal, DONE, NEVER
from mo_threads.threads import (
    MainThread,
    PLEASE_STOP,
    THREAD_TIMEOUT,
    Thread,
    stop_main_thread,
    register_thread,
    wait_for_shutdown_signal,
    start_main_thread,
    join_all_threads,
    current_thread,
)
from mo_threads.till import Till

THREAD_STOP = PLEASE_STOP
export("mo_threads.signals", threads)
del threads


def coverage_detector():
    try:
        # DETECT COVERAGE
        from coverage.collector import Collector
        from mo_threads import threads

        if Collector._collectors:
            threads.COVERAGE_COLLECTOR = Collector
    except Exception:
        pass


coverage_detector()
start_main_thread()
