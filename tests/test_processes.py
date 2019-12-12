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

import os
from unittest import skipIf

from mo_logs import Log
from mo_testing.fuzzytestcase import FuzzyTestCase

from mo_threads import Process
from mo_threads import Till
from tests import IS_WINDOWS


class TestProcesses(FuzzyTestCase):
    @classmethod
    def setUpClass(cls):
        Log.start()

    @classmethod
    def tearDownClass(cls):
        Log.stop()

    @skipIf(IS_WINDOWS, "the keyboard input and stdin are different")
    def test_exit(self):
        p = Process(
            "waiting", ["python", "-u", "tests/programs/exit_test.py"], debug=True
        )
        p.stdout.pop()  # WAIT FOR PROCESS TO START
        Till(seconds=2).wait()
        p.stdin.add("exit\n")
        p.join()
        self.assertTrue(any("EXIT DETECTED" in line for line in p.stdout.pop_all()))

    @skipIf(IS_WINDOWS, "Can not SIGINT on Windows")
    def test_sigint_no_exit(self):
        """
        CAN WE CATCH A SIGINT?
        """
        p = Process(
            "waiting", ["python", "-u", "tests/programs/exit_test.py"], debug=True
        )
        p.stdout.pop()  # WAIT FOR PROCESS TO START
        Till(seconds=2).wait()
        k = Process("killer", ["kill", "-SIGINT", p.pid])
        p.join()
        self.assertTrue(any("EXIT DETECTED" in line for line in p.stdout.pop_all()))

    def test_sigint(self):
        """
        CAN WE CATCH A SIGINT?
        """
        p = Process(
            "waiting", ["python", "-u", "tests/programs/sigint_test.py"], debug=True
        )
        p.stdout.pop()  # WAIT FOR PROCESS TO START
        Till(seconds=2).wait()
        if IS_WINDOWS:
            os.kill(p.pid)
        else:
            Process("killer", ["kill", "-SIGINT", p.pid])
        p.join()
        self.assertTrue(any("EXIT DETECTED" in line for line in p.stdout.pop_all()))

    def test_no_sigint(self):
        """
        DO WE STILL EXIT WITHOUT SIGINT?
        """
        p = Process(
            "waiting", ["python", "-u", "tests/programs/sigint_test.py"], debug=True
        )
        p.stdout.pop()  # WAIT FOR PROCESS TO START
        Till(seconds=2).wait()
        p.join()
        self.assertTrue(not any("EXIT DETECTED" in line for line in p.stdout.pop_all()))

    @skipIf(IS_WINDOWS, "Can not SIGTERM on Windows")
    def test_sigterm(self):
        """
        CAN WE CATCH A SIGINT?
        """
        p = Process(
            "waiting", ["python", "-u", "tests/programs/sigint_test.py"], debug=True
        )
        p.stdout.pop()  # WAIT FOR PROCESS TO START
        Till(seconds=2).wait()
        k = Process("killer", ["kill", "-SIGTERM", p.pid])
        p.join()
        self.assertTrue(any("EXIT DETECTED" in line for line in p.stdout.pop_all()))

    def test_self_stop(self):
        """
        CAN PROCESS STOP ITSELF??
        """
        p = Process(
            "waiting", ["python", "-u", "tests/programs/stop_test.py"], debug=True
        )
        p.join()
        self.assertTrue(any("EXIT DETECTED" in line for line in p.stdout.pop_all()))
