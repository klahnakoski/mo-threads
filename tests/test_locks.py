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
import threading
from time import time
from unittest import skipIf, skip

import objgraph
import psutil
from mo_collections.queue import Queue
from mo_files import File
from mo_future import allocate_lock as _allocate_lock
from mo_logs import logger, machine_metadata, get_stacktrace
from mo_math import randoms
from mo_testing.fuzzytestcase import FuzzyTestCase
from mo_times.timer import Timer

import mo_threads
from mo_threads import Lock, THREAD_STOP, Signal, Thread, ThreadedQueue, Till
from mo_threads.busy_lock import BusyLock
from mo_threads.signals import OrSignal
from mo_threads.threads import ALL, ALL_LOCK, start_main_thread
from tests import StructuredLogger_usingList
from tests.utils import add_error_reporting

USE_PYTHON_THREADS = False
DEBUG_SHOW_BACKREFS = True
IN_DEBUGGER = any("pydevd.py" in line["file"] for line in get_stacktrace())
IN_COVERAGE = any("coverage/execfile.py" in line["file"].replace("\\", "/") for line in get_stacktrace())

print(f"IN_DEBUGGER={IN_DEBUGGER}")
print(f"IN_COVERAGE={IN_COVERAGE}")
print(get_stacktrace())


@add_error_reporting
class TestLocks(FuzzyTestCase):
    @classmethod
    def setUpClass(cls):
        logger.start({"trace": True, "cprofile": False})

    @classmethod
    def tearDownClass(cls):
        logger.stop()

    def setUp(self):
        start_main_thread()
        self.old, logger.main_log = logger.main_log, StructuredLogger_usingList()

    def tearDown(self):
        self.logs, logger.main_log = logger.main_log, self.old

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

    @skipIf(IN_DEBUGGER or IN_COVERAGE, "The debugger is too slow")
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

        timer = Timer("add {num} to queue", param={"num": SCALE})
        with timer:
            for i in range(SCALE):
                q.add(i)
            q.add(THREAD_STOP)
            logger.info("Done insert")
            done.wait()

        logger.info(
            "{num} items through queue in {seconds|round(3)} seconds", num=SCALE, seconds=timer.duration.seconds,
        )
        expected_time = 6
        if test:
            self.assertLess(
                timer.duration.seconds,
                expected_time,
                f"Expecting queue to be fast, not {timer.duration.seconds} seconds",
            )

    def test_lock_and_till(self):
        locker = Lock("prime lock")
        got_signal = Signal()
        a_is_ready = Signal("a lock")
        b_is_ready = Signal("b lock")

        logger.info("begin")

        def loop(is_ready, please_stop):
            with locker:
                while not got_signal:
                    locker.wait(till=Till(seconds=0.01))
                    is_ready.go()
                    logger.info("{thread} is ready", thread=Thread.current().name)
                logger.info("outside loop")
                locker.wait()
                logger.info("thread is expected to get here")

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
                logger.info("wait for a thread")
            while not thread_b.stopped:
                # WE MUST CONTINUE TO USE THE locker TO ENSURE THE OTHER THREADS ARE NOT ORPHANED IN THERE
                locker.wait(till=Till(seconds=0.1))
                logger.info("wait for b thread")
        thread_a.join()
        thread_b.join()
        if timeout:
            logger.error("Took too long")

        self.assertTrue(bool(thread_a.stopped), "Thread should be done by now")
        self.assertTrue(bool(thread_b.stopped), "Thread should be done by now")

    @skipIf(IN_DEBUGGER or IN_COVERAGE, "The debugger is too slow")
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
            len(tills), 60000, f"Till objects must be created faster: {len(tills)} per second is too slow",
        )
        logger.info("{num} new Tills in one second", num=len(tills))

    def test_till_in_loop(self):
        def loop(please_stop):
            counter = 0
            while not please_stop:
                (Till(seconds=0.001) | please_stop).wait()
                counter += 1
                logger.info("{count}", count=counter)
            logger.info("loop done")

        please_stop = Signal("please_stop")
        Thread.run("loop", loop, please_stop=please_stop)
        Till(seconds=1).wait()
        with please_stop.lock:
            q = please_stop.job_queue
            self.assertLessEqual(
                0 if q is None else len(q), 1, f"Expecting only one pending job on go, got {len(q)}",
            )
        please_stop.go()
        logger.info("test done")

    def test_consistency(self):
        counter = [0]
        lock = BusyLock()

        def adder(please_stop):
            for i in range(100):
                with lock:
                    counter[0] += 1

        threads = [Thread.run(str(i), adder) for i in range(50)]
        for t in threads:
            t.join()

        self.assertEqual(counter[0], 100 * 50, "Expecting lock to work")

    @skip
    def test_memory_cleanup_with_till(self):
        interesting = [Signal.__name__, OrSignal.__name__, Till.__name__]
        root = Signal()
        start_ids = set(id(o) for o in gc.get_objects())
        start_counts = {n: objgraph.count(n) for n in interesting}
        start_ids.add(id(start_ids))
        start_ids.add(id(start_counts))

        for i in range(100000):
            if i % 1000 == 0:
                logger.info("at {num} tills", num=i)
            root = root | Till(seconds=100000)
            mid_mem = psutil.Process(os.getpid()).memory_info().rss
            if mid_mem > 1000 * 1000 * 1000:
                logger.info("{num} Till triggers created", num=i)
                break
        trigger = Signal()
        root = root | trigger

        growth_counts = {n: objgraph.count(n) for n in interesting}
        growth_counts and logger.info("More object\n{growth}", growth=growth_counts)

        trigger.go()
        del trigger
        root.wait()  # THERE SHOULD BE NO DELAY HERE

        for _ in range(0, 20):
            try:
                waiter = Till(seconds=0.1)
                waiter.wait()  # LET TIMER DAEMON CLEANUP
                gc.collect()
                stop_counts = {n: objgraph.count(n) - start_counts[n] for n in interesting}
                logger.info("Object count\n{current}", current=stop_counts)

                # THERE SHOULD BE NO NET NEW OBJECTS
                for name, net_new in stop_counts.items():
                    self.assertLessEqual(net_new, 0, f"Object {name} went up by {net_new}")
                return
            except Exception as cause:
                print(f"problem: {cause}")
                cause_description = str(cause)
                del cause
                remaining = [o for o in objgraph.by_type("method") if id(o) not in start_ids]
                examples = remaining  # randoms.sample(remaining, 1)
                filename = "test_memory_cleanup_with_till.png"
                try:
                    File(filename).backup()
                except Exception:
                    pass
                objgraph.show_backrefs(examples, max_depth=10, filename=File(filename).os_path)
                del remaining
                del examples
                del filename
                logger.info("problem: {cause}", cause=cause_description)
        logger.error("object counts did not go down")

    @skipIf(IN_DEBUGGER or IN_COVERAGE, "The debugger is too slow")
    def test_job_queue_in_signal(self):

        gc.collect()
        start_mem = psutil.Process(os.getpid()).memory_info().rss
        logger.info("Start memory {mem|comma}", mem=start_mem)

        main = Signal()
        result = [main | Signal() for _ in range(10000)]

        mid_mem = psutil.Process(os.getpid()).memory_info().rss
        logger.info("Mid memory {mem|comma}", mem=mid_mem)

        del result
        gc.collect()

        end_mem = psutil.Process(os.getpid()).memory_info().rss
        logger.info("End memory {mem|comma}", mem=end_mem)

        main.go()  # NOT NEEDED, BUT INTERESTING

        self.assertLess(end_mem, (start_mem + mid_mem) / 2, "end memory should be closer to start")

    def test_relase_lock_failure(self):
        lock = _allocate_lock()
        with self.assertRaises(RuntimeError):
            lock.release()

    @skipIf(IN_DEBUGGER or IN_COVERAGE, "The debugger hangs onto threads ")
    def test_memory_cleanup_with_signal(self):
        """
        LOOKING FOR A MEMORY LEAK THAT HAPPENS ONLY DURING THREADING

        ACTUALLY, THE PARTICULAR LEAK FOUND CAN BE RECREATED WITHOUT THREADS
        BUT IT IS TOO LATE TO CHANGE THIS TEST
        """
        NUM_CYCLES = 100
        gc.collect()
        start_mem = psutil.Process(os.getpid()).memory_info().rss
        logger.info("Start memory {mem|comma}", mem=start_mem)

        queue = mo_threads.Queue("", max=1000000)

        def _consumer(please_stop):
            while not please_stop:
                v = queue.pop(till=please_stop)
                if randoms.int(1000) == 0:
                    logger.info("got {v}", v=v)

        def _producer(t, please_stop=None):
            for i in range(2):
                queue.add(str(t) + ":" + str(i))
                Till(seconds=0.01).wait()

        consumer = Thread.run("consumer", _consumer)

        interesting = [Signal.__name__, OrSignal.__name__, Till.__name__, Thread.__name__]
        start_ids = set(id(o) for o in gc.get_objects())
        start_counts = {n: objgraph.count(n) for n in interesting}
        start_ids.add(id(start_ids))
        start_ids.add(id(start_counts))

        no_change = 0
        for g in range(NUM_CYCLES):
            mid_mem = psutil.Process(os.getpid()).memory_info().rss
            logger.info("{group} memory {mem|comma}", group=g, mem=mid_mem)
            if USE_PYTHON_THREADS:
                threads = [threading.Thread(target=_producer, args=(i,)) for i in range(500)]
                for t in threads:
                    t.start()
            else:
                threads = [Thread.run(f"producer-{i}", _producer, i) for i in range(500)]

            Thread.join_all(threads)

            while threads:
                Till(seconds=0.1).wait()
                with ALL_LOCK:
                    residue = list(ALL)
                threads = [t for t in threads if t.ident in residue]
                if threads:
                    logger.error("Threads still running: {residue}", residue=residue)
            del threads

            gc.collect()
            end_counts = {n: objgraph.count(n) for n in interesting}
            end_ids = set(id(o) for o in gc.get_objects())

            growth_seen = any(end_counts[n] - start_counts[n] > 0 for n in interesting)
            if not growth_seen:
                no_change += 1
            else:
                logger.info("growth = \n{results}", results=growth_seen)
                filename = "test_memory_cleanup_with_signal.png"
                try:
                    File(filename).backup()
                except Exception:
                    pass
                examples = [o for o in gc.get_objects() if id(o) not in start_ids and type(o).__name__ in interesting]
                objgraph.show_backrefs(examples[0], max_depth=4, filename=File(filename).os_path)
            start_counts = {n: max(end_counts[n], start_counts[n]) for n in interesting}
            start_ids = end_ids

        consumer.please_stop.go()
        consumer.join()

        self.assertGreater(
            no_change, NUM_CYCLES / 2
        )  # IF MOST CYCLES DO NOT HAVE MORE OBJECTS, WE ASSUME THERE IS NO LEAK
