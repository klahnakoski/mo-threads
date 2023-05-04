# encoding: utf-8
#
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Author: Kyle Lahnakoski (kyle@lahnakoski.com)
#


import gc
import os
import platform
import threading
from time import time

import objgraph
import psutil
from mo_collections.queue import Queue
from mo_future import allocate_lock as _allocate_lock, text, PY2, PY3
from mo_logs import Log, machine_metadata
from mo_math import randoms
from mo_testing.fuzzytestcase import FuzzyTestCase
from mo_times.timer import Timer

import mo_threads
from mo_threads import Lock, THREAD_STOP, Signal, Thread, ThreadedQueue, Till
from mo_threads.busy_lock import BusyLock
from tests import StructuredLogger_usingList

USE_PYTHON_THREADS = False
DEBUG_SHOW_BACKREFS = False


class TestLocks(FuzzyTestCase):
    @classmethod
    def setUpClass(cls):
        Log.start({"trace": True, "cprofile": False})

    @classmethod
    def tearDownClass(cls):
        Log.stop()

    def setUp(self):
        self.old, Log.main_log = Log.main_log, StructuredLogger_usingList()

    def tearDown(self):
        self.logs, Log.main_log = Log.main_log, self.old

    def test_signal_is_not_null(self):
        a = Signal()
        self.assertNotEqual(a, None)
        a.go()
        self.assertNotEqual(a, None)

    def test_signal_is_boolean(self):
        a = Signal()
        self.assertEqual(bool(a), False)
        a.go()
        self.assertEqual(bool(a), True)

    def test_lock_speed(self):
        SCALE = 1000 * 100

        with Timer("create"):
            locks = [_allocate_lock() for _ in range(SCALE)]

        with Timer("acquire"):
            for i in range(SCALE):
                locks[i].acquire()

        with Timer("release"):
            for i in range(SCALE):
                locks[i].release()

    def test_queue_speed(self):
        if "PyPy" in machine_metadata().python:
            # PyPy requires some warmup time
            self._test_queue_speed()
            self._test_queue_speed()
            Till(seconds=1).wait()
        self._test_queue_speed(test=True)

    def _test_queue_speed(self, test=False):
        SCALE = 1000 * 10

        done = Signal("done")
        slow = Queue()
        q = ThreadedQueue("test queue", slow_queue=slow)

        def empty(please_stop):
            while not please_stop:
                item = slow.pop()
                if item is THREAD_STOP:
                    break

            done.go()

        Thread.run("empty", empty)

        timer = Timer("add {{num}} to queue", param={"num": SCALE})
        with timer:
            for i in range(SCALE):
                q.add(i)
            q.add(THREAD_STOP)
            Log.note("Done insert")
            done.wait()

        Log.note(
            "{{num}} items through queue in {{seconds|round(3)}} seconds", num=SCALE, seconds=timer.duration.seconds,
        )
        if PY2 and "windows" not in platform.system().lower():
            expected_time = 15  # LINUX PY2 IS CRAZY SLOW
        elif PY3 and "windows" not in platform.system().lower():
            expected_time = 6  # LINUX PY3 IS SLOW
        else:
            expected_time = 6
        if test:
            self.assertLess(
                timer.duration.seconds,
                expected_time,
                "Expecting queue to be fast, not " + text(timer.duration.seconds) + " seconds",
            )

    def test_lock_and_till(self):
        locker = Lock("prime lock")
        got_signal = Signal()
        a_is_ready = Signal("a lock")
        b_is_ready = Signal("b lock")

        Log.note("begin")

        def loop(is_ready, please_stop):
            with locker:
                while not got_signal:
                    locker.wait(till=Till(seconds=0.01))
                    is_ready.go()
                    Log.note("{{thread}} is ready", thread=Thread.current().name)
                Log.note("outside loop")
                locker.wait()
                Log.note("thread is expected to get here")

        thread_a = Thread.run("a", loop, a_is_ready).release()
        thread_b = Thread.run("b", loop, b_is_ready).release()

        a_is_ready.wait()
        b_is_ready.wait()
        timeout = Till(seconds=1)
        with locker:
            got_signal.go()
            while not thread_a.stopped:
                # WE MUST CONTINUE TO USE THE locker TO ENSURE THE OTHER THREADS ARE NOT ORPHANED IN THERE
                locker.wait(till=Till(seconds=0.1))
                Log.note("wait for a thread")
            while not thread_b.stopped:
                # WE MUST CONTINUE TO USE THE locker TO ENSURE THE OTHER THREADS ARE NOT ORPHANED IN THERE
                locker.wait(till=Till(seconds=0.1))
                Log.note("wait for b thread")
        thread_a.join()
        thread_b.join()
        if timeout:
            Log.error("Took too long")

        self.assertTrue(bool(thread_a.stopped), "Thread should be done by now")
        self.assertTrue(bool(thread_b.stopped), "Thread should be done by now")

    def test_till_create_speed(self):
        tills = []
        done = time() + 1

        def loop(please_stop):
            while not please_stop:
                tills.append(Till(till=done))

        ps = Till(till=done)
        thread = Thread.run("test", loop, please_stop=ps)
        thread.join()

        self.assertGreater(
            len(tills), 60000, "Till objects must be created faster: " + text(len(tills)) + " per second is too slow",
        )
        Log.note("{{num}} new Tills in one second", num=len(tills))

    def test_till_in_loop(self):
        def loop(please_stop):
            counter = 0
            while not please_stop:
                (Till(seconds=0.001) | please_stop).wait()
                counter += 1
                Log.note("{{count}}", count=counter)
            Log.note("loop done")

        please_stop = Signal("please_stop")
        Thread.run("loop", loop, please_stop=please_stop)
        Till(seconds=1).wait()
        with please_stop.lock:
            q = please_stop.job_queue
            self.assertLessEqual(
                0 if q is None else len(q), 1, "Expecting only one pending job on go, got " + text(len(q)),
            )
        please_stop.go()
        Log.note("test done")

    def test_consistency(self):
        counter = [0]
        lock = BusyLock()

        def adder(please_stop):
            for i in range(100):
                with lock:
                    counter[0] += 1

        threads = [Thread.run(text(i), adder) for i in range(50)]
        for t in threads:
            t.join()

        self.assertEqual(counter[0], 100 * 50, "Expecting lock to work")

    def test_memory_cleanup_with_till(self):
        objgraph.growth()

        root = Signal()
        for i in range(100000):
            if i % 1000 == 0:
                Log.note("at {{num}} tills", num=i)
            root = root | Till(seconds=100000)
            mid_mem = psutil.Process(os.getpid()).memory_info().rss
            if mid_mem > 1000 * 1000 * 1000:
                Log.note("{{num}} Till triggers created", num=i)
                break
        trigger = Signal()
        root = root | trigger

        growth = objgraph.growth(limit=4)
        growth and Log.note("More object\n{{growth}}", growth=growth)

        trigger.go()
        root.wait()  # THERE SHOULD BE NO DELAY HERE

        for _ in range(0, 20):
            try:
                Till(seconds=0.1).wait()  # LET TIMER DAEMON CLEANUP
                current = [(t, objgraph.count(t), objgraph.count(t) - c) for t, c, d in growth]
                Log.note("Object count\n{{current}}", current=current)

                # NUMBER OF OBJECTS CLEANED UP SHOULD BE SAME, OR BIGGER THAN NUMBER OF OBJECTS CREATED
                for (_, _, cd), (_, gt, gd) in zip(current, growth):
                    self.assertGreater(-cd, gd)
                return
            except Exception as cause:
                Log.note("problem: {{cause}}", cause=cause)
        Log.error("object counts did not go down")

    def test_job_queue_in_signal(self):

        gc.collect()
        start_mem = psutil.Process(os.getpid()).memory_info().rss
        Log.note("Start memory {{mem|comma}}", mem=start_mem)

        main = Signal()
        result = [main | Signal() for _ in range(10000)]

        mid_mem = psutil.Process(os.getpid()).memory_info().rss
        Log.note("Mid memory {{mem|comma}}", mem=mid_mem)

        del result
        gc.collect()

        end_mem = psutil.Process(os.getpid()).memory_info().rss
        Log.note("End memory {{mem|comma}}", mem=end_mem)

        main.go()  # NOT NEEDED, BUT INTERESTING

        self.assertLess(end_mem, (start_mem + mid_mem) / 2, "end memory should be closer to start")

    def test_relase_lock_failure(self):
        lock = _allocate_lock()
        with self.assertRaises(RuntimeError):
            lock.release()

    def test_memory_cleanup_with_signal(self):
        """
        LOOKING FOR A MEMORY LEAK THAT HAPPENS ONLY DURING THREADING

        ACTUALLY, THE PARTICULAR LEAK FOUND CAN BE RECREATED WITHOUT THREADS
        BUT IT IS TOO LATE TO CHANGE THIS TEST
        """
        NUM_CYCLES = 100
        gc.collect()
        start_mem = psutil.Process(os.getpid()).memory_info().rss
        Log.note("Start memory {{mem|comma}}", mem=start_mem)

        queue = mo_threads.Queue("", max=1000000)

        def _consumer(please_stop):
            while not please_stop:
                v = queue.pop(till=please_stop)
                if randoms.int(1000) == 0:
                    Log.note("got " + v)

        def _producer(t, please_stop=None):
            for i in range(2):
                queue.add(str(t) + ":" + str(i))
                Till(seconds=0.01).wait()

        consumer = Thread.run("", _consumer)

        objgraph.growth(limit=None)

        no_change = 0
        for g in range(NUM_CYCLES):
            mid_mem = psutil.Process(os.getpid()).memory_info().rss
            Log.note("{{group}} memory {{mem|comma}}", group=g, mem=mid_mem)
            if USE_PYTHON_THREADS:
                threads = [threading.Thread(target=_producer, args=(i,)) for i in range(500)]
                for t in threads:
                    t.start()
            else:
                threads = [Thread.run("", _producer, i) for i in range(500)]

            for t in threads:
                t.join()
            del threads

            gc.collect()
            results = objgraph.growth(limit=3)
            if not results:
                no_change += 1
            else:
                if DEBUG_SHOW_BACKREFS:
                    for typ, count, delta in results:
                        Log.note("%-*s%9d %+9d\n" % (18, typ, count, delta))
                        obj_list = objgraph.by_type(typ)
                        if obj_list:
                            obj = obj_list[-1]
                            objgraph.show_backrefs(obj, max_depth=10)
                else:
                    Log.note("growth = \n{{results}}", results=results)

        consumer.please_stop.go()
        consumer.join()

        self.assertGreater(
            no_change, NUM_CYCLES / 2
        )  # IF MOST CYCLES DO NOT HAVE MORE OBJECTS, WE ASSUME THERE IS NO LEAK
