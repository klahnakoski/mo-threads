# encoding: utf-8
#
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Author: Kyle Lahnakoski (kyle@lahnakoski.com)
#
import os
import sys
from unittest import skipIf

from mo_logs import Log
from mo_testing.fuzzytestcase import FuzzyTestCase

from mo_threads import Process
from mo_threads import Till
from tests import IS_WINDOWS

IS_TRAVIS = bool(os.environ.get("TRAVIS"))


class TestProcesses(FuzzyTestCase):
    @classmethod
    def setUpClass(cls):
        Log.start()

    @classmethod
    def tearDownClass(cls):
        Log.stop()

    def test_exit(self):
        p = Process("waiting", [sys.executable, "-u", "tests/programs/exit_test.py"], debug=True)
        p.stdout.pop()  # WAIT FOR PROCESS TO START
        Till(seconds=2).wait()
        p.stdin.add("exit\n")
        p.join()
        self.assertTrue(any("EXIT DETECTED" in line for line in p.stdout.pop_all()))

    @skipIf(IS_TRAVIS or IS_WINDOWS, "Can not SIGINT on Windows")
    def test_sigint_no_exit(self):
        """
        CAN WE CATCH A SIGINT?
        """
        p = Process("waiting", [sys.executable, "-u", "tests/programs/exit_test.py"], debug=True)
        p.stdout.pop()  # WAIT FOR PROCESS TO START
        Till(seconds=2).wait()
        command = ["kill", "-s", "int", p.pid]
        k = Process("killer", command, shell=True)
        k.join(raise_on_error=True)
        p.join()
        self.assertTrue(any("EXIT DETECTED" in line for line in p.stdout.pop_all()))

    @skipIf(
        IS_TRAVIS or IS_WINDOWS, "travis can not kill, Python can not send ctrl-c on Windows",
    )
    def test_sigint(self):
        """
        CAN WE CATCH A SIGINT?
        """
        p = Process("waiting", [sys.executable, "-u", "tests/programs/sigint_test.py"], debug=True)
        p.stdout.pop()  # WAIT FOR PROCESS TO START
        if IS_WINDOWS:
            import signal

            os.kill(p.pid, signal.CTRL_C_EVENT)
        else:
            Process("killer", ["kill", "-SIGINT", p.pid]).join()
        p.join()
        self.assertTrue(any("EXIT DETECTED" in line for line in p.stdout.pop_all()))

    def test_no_sigint(self):
        """
        DO WE STILL EXIT WITHOUT SIGINT?
        """
        p = Process("waiting", [sys.executable, "-u", "tests/programs/sigint_test.py"], debug=True)
        p.stdout.pop()  # WAIT FOR PROCESS TO START
        Till(seconds=2).wait()
        p.join(raise_on_error=True)
        self.assertTrue(not any("EXIT DETECTED" in line for line in p.stdout.pop_all()))

    @skipIf(IS_TRAVIS or IS_WINDOWS, "Can not SIGTERM on Windows or Travis")
    def test_sigterm(self):
        """
        CAN WE CATCH A SIGINT?
        """
        p = Process("waiting", [sys.executable, "-u", "tests/programs/sigint_test.py"], debug=True)
        p.stdout.pop()  # WAIT FOR PROCESS TO START
        Till(seconds=2).wait()
        Process("killer", ["kill", "-SIGTERM", p.pid]).join()
        p.join()
        self.assertTrue(any("EXIT DETECTED" in line for line in p.stdout.pop_all()))

    def test_self_stop(self):
        """
        CAN PROCESS STOP ITSELF??
        """
        p = Process("waiting", [sys.executable, "-u", "tests/programs/stop_test.py"], debug=True)
        p.join()
        self.assertTrue(any("EXIT DETECTED" in line for line in p.stdout.pop_all()))

    def test_stop_does_not_throw_after_warning(self):
        p = Process("waiting", [sys.executable, "-u", "tests/programs/simple_test.py"], debug=True)
        p.join()
        lines = p.stdout.pop_all()
        self.assertIn("All threads have shutdown", lines)
