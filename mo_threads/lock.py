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


from mo_future import allocate_lock as _allocate_lock, decorate
from mo_logs.strings import quote
from mo_logs import logger

from mo_threads.signals import Signal

DEBUG = False


class Lock(object):
    """
    A NON-RE-ENTRANT LOCK WITH wait()
    """

    __slots__ = ["name", "debug", "sample", "lock", "waiting"]

    def __init__(self, name="", debug=DEBUG, sample=False):
        self.debug = debug
        self.sample = sample
        self.name = name
        self.lock = _allocate_lock()
        self.waiting = None

    def __enter__(self):
        self.debug and print(f"acquire  lock {quote(self.name)}")
        self.lock.acquire()
        self.debug and print(f"acquired lock {quote(self.name)}")
        return self

    def __exit__(self, a, b, c):
        if self.waiting:
            self.debug and print(f"signaling {len(self.waiting)} waiters on {quote(self.name)}")
            # TELL ANOTHER THAT THE LOCK IS READY SOON
            waiter = self.waiting.pop()
            waiter.go()
        self.lock.release()
        self.debug and print(f"released lock {quote(self.name)}")

    def wait(self, till=None):
        """
        THE ASSUMPTION IS wait() WILL ALWAYS RETURN WITH THE LOCK ACQUIRED
        :param till: WHEN TO GIVE UP WAITING FOR ANOTHER THREAD TO SIGNAL, LOCK IS STILL ACQUIRED
        :return: True IF SIGNALED TO GO, False IF till WAS SIGNALED
        """
        self.debug and print(f"wait on {self.name}")
        waiter = Signal()
        if self.waiting:
            # TELL ANOTHER THAT THE LOCK IS READY SOON
            self.debug and print(f"inform other on {quote(self.name)}")
            other = self.waiting.pop()
            other.go()
            self.debug and print(f"waiting with {len(self.waiting)} others on {quote(self.name)}")
            self.waiting.insert(0, waiter)
        else:
            self.debug and print(f"waiting by self on {quote(self.name)}")
            self.waiting = [waiter]

        both = waiter | till
        try:
            self.lock.release()
            self.debug and print(f"out of lock {quote(self.name)}")
            self.debug and print(f"wait on  {both.name}")
            both.wait()
            self.debug and print(f"done minimum wait (for signal {till.name})")
        except Exception as cause:
            logger.warning("problem", cause=cause)
        finally:
            self.lock.acquire()
            self.debug and print(f"re-acquired lock: {quote(self.name)}")

        try:
            self.waiting.remove(waiter)
            self.debug and print(f"removed own signal from {quote(self.name)}")
        except Exception:
            pass

        return bool(waiter)


def locked(func):
    """
    WRAP func WITH A Lock, TO ENSURE JUST ONE THREAD AT A TIME
    """
    lock = Lock()

    @decorate(func)
    def output(*args, **kwargs):
        with lock:
            return func(*args, **kwargs)

    return output
