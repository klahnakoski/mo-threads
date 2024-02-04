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

from mo_logs import logger
from mo_testing.fuzzytestcase import FuzzyTestCase

from mo_threads import Process, start_main_thread, Command
from mo_threads import Till
from tests import IS_WINDOWS
from tests.utils import add_error_reporting

IS_TRAVIS = bool(os.environ.get("TRAVIS"))


@add_error_reporting
class TestProcesses(FuzzyTestCase):
    @classmethod
    def setUpClass(cls):
        start_main_thread()
        logger.start(trace=True)

    def test_exit(self):
        p = Process("run exit_test", [sys.executable, "-u", "tests/programs/exit_test.py"], debug=True)
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
        p = Process("run exit_test", [sys.executable, "-u", "tests/programs/exit_test.py"], debug=True)
        p.stdout.pop()  # WAIT FOR PROCESS TO START
        Till(seconds=2).wait()
        logger.alert("SENDING SIGINT to {{pid}}", pid=p.pid)
        command = ["kill", "-s", "int", p.pid]
        Process(f"kill {p.pid}", command, shell=False).join(raise_on_error=False)
        p.join(raise_on_error=False)  # TODO: Fix this so we can raise_on_error=True
        self.assertTrue(any("EXIT DETECTED" in line for line in p.stdout.pop_all()))

    @skipIf(IS_TRAVIS or IS_WINDOWS, "travis can not kill, Python can not send ctrl-c on Windows")
    def test_sigint(self):
        """
        CAN WE CATCH A SIGINT?
        """
        p = Process("run sigint test", [sys.executable, "-u", "tests/programs/sigint_test.py"], debug=True)
        p.stdout.pop()  # WAIT FOR PROCESS TO START
        if IS_WINDOWS:
            import signal

            os.kill(p.pid, signal.CTRL_C_EVENT)
        else:
            Process(f"kill {p.pid}", ["kill", "-SIGINT", p.pid]).join(raise_on_error=False)
        p.join()
        self.assertTrue(any("EXIT DETECTED" in line for line in p.stdout.pop_all()))

    @skipIf(IS_TRAVIS or IS_WINDOWS, "Can not SIGINT on Windows or Travis")
    def test_no_sigint(self):
        """
        DO WE STILL EXIT WITHOUT SIGINT?
        """
        p = Process("run no_sigint test", [sys.executable, "-u", "tests/programs/sigint_test.py"], debug=True)
        p.stdout.pop()  # WAIT FOR PROCESS TO START
        Till(seconds=2).wait()
        p.join(raise_on_error=True)
        self.assertTrue(not any("EXIT DETECTED" in line for line in p.stdout.pop_all()))

    @skipIf(IS_TRAVIS or IS_WINDOWS, "Can not SIGTERM on Windows or Travis")
    def test_sigterm(self):
        """
        CAN WE CATCH A SIGINT?
        """
        p = Process("run sigterm test", [sys.executable, "-u", "tests/programs/sigint_test.py"], debug=True)
        p.stdout.pop()  # WAIT FOR PROCESS TO START
        Till(seconds=2).wait()
        Process(f"kill {p.pid}", ["kill", "-SIGTERM", p.pid]).join(raise_on_error=False)
        p.join()
        self.assertTrue(any("EXIT DETECTED" in line for line in p.stdout.pop_all()))

    def test_self_stop(self):
        """
        CAN PROCESS STOP ITSELF??
        """
        p = Process("run stop_test", [sys.executable, "-u", "tests/programs/stop_test.py"], debug=True, timeout=10)
        p.join()
        self.assertTrue(any("EXIT DETECTED" in line for line in p.stdout.pop_all()))

    def test_stop_does_not_throw_after_warning(self):
        p = Process("run simple_test", [sys.executable, "-u", "tests/programs/simple_test.py"], debug=True)
        p.join()
        lines = p.stdout.pop_all()
        self.assertIn("All threads have shutdown", lines)

    def test_command_shutdown(self):
        Command("test", [sys.executable, "-c", "print('test')"]).join()
