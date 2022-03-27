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

from mo_future import text
from mo_logs import Log
from mo_testing.fuzzytestcase import FuzzyTestCase
from mo_times.dates import Date
from mo_times.durations import SECOND

from mo_threads import Lock, Thread, Signal, Till, till, threads, start_main_thread
from mo_threads.threads import wait_for_shutdown_signal, stop_main_thread
from tests import StructuredLogger_usingList


class TestThreads(FuzzyTestCase):
    def setUp(self):
        old_log, Log.main_log = Log.main_log, StructuredLogger_usingList()
        old_log.stop()

    def tearDown(self):
        Log.stop()

    def test_lock_wait_timeout(self):
        locker = Lock("test")

        def take_lock(please_stop):
            with locker:
                locker.wait(Till(seconds=1))
                locker.wait(Till(seconds=1))
                locker.wait(Till(till=(Date.now() + SECOND).unix))

        t = Thread.run("take lock", take_lock)
        t.join()

    def test_thread_wait(self):
        NUM = 100
        locker = Lock("test")
        phase1 = []
        phase2 = []

        def work(value, please_stop):
            with locker:
                phase1.append(value)
                locker.wait()
                phase2.append(value)

        with locker:
            threads = [Thread.run(text(i), work, i) for i in range(NUM)]

        # CONTINUE TO USE THE locker SO WAITS GET TRIGGERED

        while len(phase2) < NUM:
            with locker:
                pass
        for t in threads:
            t.join()

        self.assertEqual(len(phase1), NUM, "expecting " + text(NUM) + " items")
        self.assertEqual(len(phase2), NUM, "expecting " + text(NUM) + " items")
        for i in range(NUM):
            self.assertTrue(i in phase1, "expecting " + text(i))
            self.assertTrue(i in phase2, "expecting " + text(i))

    def test_thread_wait_till(self):
        phase1 = []
        phase2 = []

        def work(value, please_stop):
            with Lock() as locker:
                phase1.append(value)
                locker.wait(Till(seconds=0.1))
                phase2.append(value)

        worker = Thread.run("worker", work, 0)
        worker.join()
        worker.stopped.wait()

        self.assertEqual(phase1, [0], "expecting ordered list")
        self.assertEqual(phase2, [0], "expecting ordered list")

    def test_timeout(self):
        def test(please_stop):
            Till(seconds=10).wait()

        now = Date.now()
        thread = Thread.run("sleeper", test)
        Till(seconds=0.5).wait()
        thread.stop()
        self.assertGreater(now.unix + 1, Date.now().unix, "Expecting quick stop")

    def test_sleep(self):
        Till(seconds=0.5).wait()

    def test_loop(self):
        acc = []
        started = Signal()

        def work(please_stop):
            started.go()
            while not please_stop:
                acc.append(Date.now().unix)
                Till(seconds=0.1).wait()

        worker = Thread.run("loop", work)
        started.wait()
        while len(acc) < 10:
            Till(seconds=0.1).wait()
        worker.stop()
        worker.join()

        # We expect 10, but 9 is good enough
        num = len(acc)
        self.assertGreater(
            num,
            9,
            "Expecting some reasonable number of entries to prove there was looping,"
            " not "
            + text(num),
        )

    def test_or_signal_timeout(self):
        acc = []

        def worker(this, please_stop):
            (Till(seconds=0.3) | please_stop).wait()
            this.assertTrue(not please_stop, "Expecting not to have stopped yet")
            acc.append("worker")

        w = Thread.run("worker", worker, self)
        w.join()
        w.stopped.wait()
        acc.append("done")

        self.assertEqual(acc, ["worker", "done"])

    def test_or_signal_stop(self):
        acc = []

        def worker(this, please_stop):
            (Till(seconds=0.3) | please_stop).wait()
            this.assertTrue(not not please_stop, "Expecting to have the stop signal")
            acc.append("worker")

        w = Thread.run("worker", worker, self)
        Till(seconds=0.1).wait()
        w.stop()
        w.join()
        w.stopped.wait()
        acc.append("done")

        self.assertEqual(acc, ["worker", "done"])

    def test_and_signals(self):
        acc = []
        locker = Lock()

        def worker(please_stop):
            with locker:
                acc.append("worker")

        a = Thread.run("a", worker).release()
        b = Thread.run("b", worker).release()
        c = Thread.run("c", worker).release()

        (a.stopped & b.stopped & c.stopped).wait()
        acc.append("done")
        self.assertEqual(acc, ["worker", "worker", "worker", "done"])

    def test_disabled_till(self):
        till.enabled = Signal()
        t = Till(seconds=10000000)  # ALL NEW TIMING SIGNALS ARE A go()!
        t.wait()
        till.enabled.go()

    def test_start_stopped_thread(self):
        """
        We often spawn threads to do work; ensure the thread is at least started,
        let the thread decide how to balance please_stop and the work to be done
        """
        print("stopping", flush=True)
        stop_main_thread()
        threads.MAIN_THREAD.stopped.wait()
        print("starting", flush=True)
        start_main_thread()
        print("logging", flush=True)
        list_log = StructuredLogger_usingList()
        old_log, Log.main_log = Log.main_log, list_log
        old_log.stop()
        print("ready", flush=True)

        def worker(please_stop):
            Log.info("started")

        please_stop = Signal()
        please_stop.go()
        print("running", flush=True)
        thread = Thread.run("work", worker, please_stop=please_stop)
        thread.stopped.wait()
        self.assertIn("started", Log.main_log.lines)

    def test_failure_during_wait_for_shutdown(self):
        threads.DEBUG = True
        threads.MAIN_THREAD.stop()
        start_main_thread()
        list_log = StructuredLogger_usingList()
        old_log, Log.main_log = Log.main_log, list_log
        old_log.stop()

        Thread.run("test_failure_during_wait_for_shutdown", bad_worker)

        with self.assertRaises("bad worker failure"):
            wait_for_shutdown_signal(None, False, False)

        self.assertGreater(len(list_log.lines), 1)
        self.assertIn("logger stopped", list_log.lines)
        self.assertIn("ERROR", list_log.lines[-2])
        self.assertEqual(bool(threads.MAIN_THREAD.timers.stopped), True)

        start_main_thread()


def bad_worker(please_stop):
    raise Exception("bad worker failure")
