# encoding: utf-8
#
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Author: Kyle Lahnakoski (kyle@lahnakoski.com)
#

from __future__ import absolute_import
from __future__ import division
from __future__ import unicode_literals

import json
import threading
from time import time

import gc

import os

import objgraph
import requests
import psutil

from mo_collections.queue import Queue
from mo_future import allocate_lock as _allocate_lock, text_type
from mo_logs import Log, machine_metadata
from mo_math.randoms import Random
from mo_testing.fuzzytestcase import FuzzyTestCase

import mo_threads
from mo_threads import Lock, THREAD_STOP, Signal, Thread, ThreadedQueue, Till, till, lock
from mo_threads.busy_lock import BusyLock
from mo_times.timer import Timer

ACTIVEDATA_URL = "https://activedata.allizom.org/query"
USE_PYTHON_THREADS = False


class TestLocks(FuzzyTestCase):
    @classmethod
    def setUpClass(cls):
        Log.start({"trace": True, "cprofile": False})

    @classmethod
    def tearDownClass(cls):
        Log.stop()

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
        SCALE = 1000*100

        with Timer("create"):
            locks = [_allocate_lock() for _ in range(SCALE)]

        with Timer("acquire"):
            for i in range(SCALE):
                locks[i].acquire()

        with Timer("release"):
            for i in range(SCALE):
                locks[i].release()

    def test_queue_speed(self):
        if "PyPy" in machine_metadata.python:
            # PyPy requires some warmup time
            self._test_queue_speed()
            self._test_queue_speed()
            Till(seconds=1).wait()
        self._test_queue_speed(test=True)

    def _test_queue_speed(self, test=False):
        SCALE = 1000*10

        done = Signal("done")
        slow = Queue()
        q = ThreadedQueue("test queue", queue=slow)

        def empty(please_stop):
            while not please_stop:
                item = q.pop()
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

        if test:
            self.assertLess(timer.duration.seconds, 1.5, "Expecting queue to be fast")

    def test_lock_and_till(self):
        locker = Lock("prime lock")
        got_lock = Signal()
        a_is_ready = Signal("a lock")
        b_is_ready = Signal("b lock")

        Log.note("begin")
        def loop(is_ready, please_stop):
            with locker:
                while not got_lock:
                    locker.wait(till=Till(seconds=0))
                    is_ready.go()
                    Log.note("is ready", thread=Thread.current().name)
                Log.note("outside loop")
                locker.wait()
                Log.note("thread is expected to get here")
        thread_a = Thread.run("a", loop, a_is_ready)
        thread_b = Thread.run("b", loop, b_is_ready)

        a_is_ready.wait()
        b_is_ready.wait()
        with locker:
            got_lock.go()
            locker.wait(till=Till(seconds=0.1))
            Log.note("leaving")
            pass
        with locker:
            Log.note("leaving again")  # a AND b SHOULD BE TRIGGERED OUT OF locker.wait()
            pass
        Log.note("wait a second")
        Till(seconds=1).wait()
        Log.note("verification...")

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
        thread.stopped.wait()

        self.assertGreater(len(tills), 60000, "Till objects must be created faster: " + text_type(len(tills)) + " per second is too slow")
        Log.note("{{num}} new Tills in one second", num=len(tills))

    def test_till_in_loop(self):

        def loop(please_stop):
            counter = 0
            while not please_stop:
                (Till(seconds=0.001) | please_stop).wait()
                counter += 1
                Log.note("{{count}}", count=counter)

        please_stop=Signal("please_stop")
        Thread.run("loop", loop, please_stop=please_stop)
        Till(seconds=1).wait()
        with please_stop.lock:
            self.assertLessEqual(len(please_stop.job_queue), 1, "Expecting only one pending job on go")
        please_stop.go()

    def test_consistency(self):
        counter = [0]
        lock = BusyLock()

        def adder(please_stop):
            for i in range(100):
                with lock:
                    counter[0] += 1

        threads = [Thread.run(text_type(i), adder) for i in range(50)]
        for t in threads:
            t.join()

        self.assertEqual(counter[0], 100*50, "Expecting lock to work")

    def test_memory_cleanup_with_till(self):
        gc.collect()
        start_mem = psutil.Process(os.getpid()).memory_info().rss
        Log.note("Start memory {{mem|comma}}", mem=start_mem)
        root = Signal()
        for i in range(100000):
            root = root | Till(seconds=100000)
            mid_mem = psutil.Process(os.getpid()).memory_info().rss
            if mid_mem > 1.5 * 1000 * 1000 * 1000:
                Log.note("{{num}} Till triggers created", num=i)
                break
        trigger = Signal()
        root = root | trigger

        mid_mem = psutil.Process(os.getpid()).memory_info().rss
        Log.note("Mid memory {{mem|comma}}", mem=mid_mem)

        trigger.go()
        root.wait()  # THERE SHOULD BE NO DELAY HERE

        Till(seconds=1).wait()  # LET TIMER DAEMON CLEANUP
        gc.collect()
        end_mem = psutil.Process(os.getpid()).memory_info().rss
        Log.note("End memory {{mem|comma}}", mem=end_mem)

        self.assertLess(end_mem, (start_mem+mid_mem)/2, "memory should be closer to start")

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

        self.assertLess(end_mem, (start_mem+mid_mem)/2, "end memory should be closer to start")

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
                if Random.int(1000) == 0:
                    Log.note("got " + v)

        def _producer(t, please_stop=None):
            for i in range(2):
                queue.add(str(t)+":"+str(i))
                Till(seconds=0.01).wait()

        consumer = Thread.run("", _consumer)

        objgraph.show_growth(limit=3)

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
                for typ, count, delta in results:
                    Log.note('%-*s%9d %+9d\n' % (18, typ, count, delta))
                    obj_list = objgraph.by_type(typ)
                    if obj_list:
                        obj = obj_list[-1]
                        objgraph.show_backrefs(obj, max_depth=10)

        consumer.please_stop.go()
        consumer.join()

        self.assertGreater(no_change, NUM_CYCLES/2)  # IF MOST CYCLES DO NOT HAVE MORE OBJCETS, WE ASSUME THERE IS NO LEAK


def query_activedata(suite, platforms=None):
    query = json.dumps({
        "from": "unittest",
        "limit": 200000,
        "groupby": ["result.test"],
        "select": {"value": "result.duration", "aggregate": "average"},
        "where": {"and": [
            {"eq": {"suite": suite,
                    "build.platform": platforms
                    }},
            {"gt": {"run.timestamp": {"date": "today-week"}}}
        ]},
        "format": "list"
    })

    response = requests.post(
        ACTIVEDATA_URL,
        data=query,
        stream=True
    )
    response.raise_for_status()
    data = response.json()["data"]
    return data
