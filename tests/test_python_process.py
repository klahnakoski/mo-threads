# encoding: utf-8
#
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Author: Kyle Lahnakoski (kyle@lahnakoski.com)
#


from mo_logs import Log
from mo_testing.fuzzytestcase import FuzzyTestCase

from mo_threads.python import Python


class TestLocks(FuzzyTestCase):
    @classmethod
    def setUpClass(cls):
        Log.start({"trace": True})

    @classmethod
    def tearDownClass(cls):
        Log.stop()

    def test_stop(self):
        p = Python("test_stop", {})
        p.stop()

    def test_import(self):
        p = Python("test_import", {})
        p.import_module("tests.simple_module")
        result = p.add(1, 2)
        self.assertEqual(result, 3)
        p.stop()

    def test_assign(self):
        p = Python("test_assign", {})
        p.import_module("tests.simple_module")
        p.execute_script("temp = add(1, 2)")
        result = p.get("temp")
        self.assertEqual(result, 3)
        p.stop()
