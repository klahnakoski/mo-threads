# encoding: utf-8
#
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at https://www.mozilla.org/en-US/MPL/2.0/.
#
# Author: Kyle Lahnakoski (kyle@lahnakoski.com)
#
import os
import sys
from time import time as unix_now
from unittest import skipIf

from mo_files import TempDirectory
from mo_logs import logger
from mo_testing.fuzzytestcase import FuzzyTestCase, add_error_reporting

from mo_threads import Process, start_main_thread, Command, Till, threads, Thread, join_all_threads
from mo_threads.commands import release_shells
from tests import IS_WINDOWS

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

    def test_failed_process_removed_from_main_thread(self):
        p = Process("run simple_test", [sys.executable, "-u", "tests/programs/fail_test.py"], debug=True)
        p.join(raise_on_error=False)
        self.assertNotIn(p, threads.MAIN_THREAD.children)


@add_error_reporting
class TestShellLifetime(FuzzyTestCase):
    """
    A Command OWNS ITS SHELL, AND SHUTS IT DOWN BEFORE join() RETURNS.
    NO SHELL IS LEFT SITTING IN cwd, WHICH ON WINDOWS WOULD MAKE cwd UNDELETABLE
    """

    @classmethod
    def setUpClass(cls):
        start_main_thread()
        logger.start(trace=True)

    def test_cwd_is_free_after_join(self):
        d = TempDirectory()
        Command("probe", [sys.executable, "-c", "print('test')"], cwd=d).join()

        os.rmdir(d.os_path)
        self.assertFalse(os.path.exists(d.os_path))

    def test_cwd_is_free_after_stop(self):
        # A COMMAND WE GAVE UP ON MUST NOT LEAVE ITS SHELL BEHIND EITHER
        # (join() STILL WAITS FOR THE RUNNING COMMAND; THE SHELL CAN NOT READ "exit" UNTIL THEN)
        d = TempDirectory()
        slow = Command("slow", [sys.executable, "-c", "import time;time.sleep(3)"], cwd=d, timeout=30)
        slow.stop()
        slow.join()

        os.rmdir(d.os_path)
        self.assertFalse(os.path.exists(d.os_path))

    def test_cwd_can_be_used_again(self):
        with TempDirectory() as d:
            first = Command("first", [sys.executable, "-c", "print('test')"], cwd=d).join()
            self.assertEqual(first.returncode, 0)
            second = Command("second", [sys.executable, "-c", "print('test')"], cwd=d).join()
            self.assertEqual(second.returncode, 0)

    def test_concurrent_commands_in_same_cwd(self):
        # EACH COMMAND GETS ITS OWN SHELL, THERE IS NOTHING TO CONTEND FOR
        with TempDirectory() as d:
            commands = [
                Command(f"echo {i}", [sys.executable, "-c", f"print({i})"], cwd=d, timeout=30) for i in range(5)
            ]
            for i, c in enumerate(commands):
                c.join()
                self.assertEqual(c.returncode, 0)
                self.assertIn(str(i), c.stdout.pop_all())

    def test_release_shells_is_a_no_op(self):
        with TempDirectory() as d:
            Command("probe", [sys.executable, "-c", "print('test')"], cwd=d).join()
            self.assertEqual(release_shells(d), 0)

    def test_many_shells_in_parallel(self):
        """
        WITHOUT A POOL, EVERY Command PAYS FOR ITS OWN SHELL.  SERIALLY THAT IS ~35ms EACH,
        ALMOST ALL OF IT cmd.exe BOOTING -- WHICH IS WAIT, NOT WORK, SO IT SHOULD OVERLAP.
        OPEN 100 SHELLS AT ONCE TO SEE WHAT THE REAL COST IS
        """
        num = 100
        results = [None] * num

        def say_hi(i, please_stop=None):
            c = Command(f"hi {i}", ["echo", "hi"], timeout=60).join()
            results[i] = (c.returncode, c.stdout.pop_all())

        start = unix_now()
        workers = [Thread.run(f"say hi {i}", say_hi, i) for i in range(num)]
        join_all_threads(workers)
        duration = unix_now() - start

        logger.alert(
            "{num} shells open+hi+close in parallel: {duration} seconds total, {each} ms each",
            num=num,
            duration=round(duration, 2),
            each=round(duration / num * 1000, 1),
        )

        for i, result in enumerate(results):
            self.assertNotEqual(result, None, f"command {i} did not finish")
            returncode, lines = result
            self.assertEqual(returncode, 0)
            self.assertIn("hi", lines)