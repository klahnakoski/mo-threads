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
import signal
from unittest import skipIf

from mo_future import PY3
from mo_json import value2json
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

    def test_exit(self):
        p = Process(
            "waiting", ["python", "-u", "tests/programs/exit_test.py"], debug=True
        )
        print("wait for start")
        p.stdout.pop()  # WAIT FOR PROCESS TO START
        print("saw output")
        Till(seconds=2).wait()
        print("start exit")
        p.stdin.add("exit\n")
        print("done exit")
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
        os.kill(p.pid, signal.SIGINT)
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
            import signal
            os.kill(p.pid, signal.CTRL_C_EVENT)
        else:
            os.kill(p.pid, signal.SIGINT)
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
        p.join(raise_on_error=True)
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

