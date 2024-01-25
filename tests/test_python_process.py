# encoding: utf-8
#
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Author: Kyle Lahnakoski (kyle@lahnakoski.com)
#


from mo_logs import logger
from mo_testing.fuzzytestcase import FuzzyTestCase

from mo_threads import python_worker, start_main_thread
from mo_threads.python import Python
from mo_threads.python_worker import start
from tests.utils import add_error_reporting


@add_error_reporting
class TestLocks(FuzzyTestCase):
    def setUp(self):
        start_main_thread()
        logger.start({"trace": True})

    def tearDown(self):
        logger.stop()

    def test_stop(self):
        p = Python("test_stop", {})
        p.stop()

    def test_import(self):
        p = Python("test_import", {})
        p.import_module("tests.some_thing")
        result = p.add(1, 2)
        self.assertEqual(result, 3)
        p.stop()

    def test_assign(self):
        p = Python("test_assign", {})
        p.import_module("tests.some_thing")
        p.execute_script("temp = add(1, 2)")
        result = p.get("temp")
        self.assertEqual(result, 3)
        p.stop()

    def test_worker(self):
        class Stdin:
            def __init__(self):
                self.lines = iter([b"{}", b'{"stop": true}'])

            def readline(self):
                return next(self.lines)

        class Stdout:
            def __init__(self):
                self.lines = []

            def write(self, line):
                self.lines.append(line)

            def flush(self):
                pass

        STDOUT, STDIN, STDERR = python_worker.STDOUT, python_worker.STDIN, python_worker.STDERR
        stdout, stdin, strerr = python_worker.STDOUT, python_worker.STDIN, python_worker.STDERR = (
            Stdout(),
            Stdin(),
            Stdout(),
        )
        try:
            start()
            self.assertEqual(len([line for line in stdout.lines if not line.startswith(b'{"log"')]), 2)
        finally:
            python_worker.STDOUT, python_worker.STDIN, python_worker.STDERR = STDOUT, STDIN, STDERR
