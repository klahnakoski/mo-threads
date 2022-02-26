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

from mo_threads import till
from mo_threads.futures import Future
from mo_threads.lock import Lock
from mo_threads.multiprocess import Process, Command
from mo_threads.queues import Queue, ThreadedQueue
from mo_threads.signals import Signal, DONE
from mo_threads.threads import (
    MainThread,
    THREAD_STOP,
    THREAD_TIMEOUT,
    Thread,
    stop_main_thread,
    register_thread,
    wait_for_shutdown_signal,
    start_main_thread,
)
from mo_threads.till import Till

start_main_thread()
