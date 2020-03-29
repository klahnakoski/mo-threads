# encoding: utf-8
#
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.
#

###############################################################################
# Intended to test exit behaviour from timeout, SIGINT (CTRL-C), or "exit"
###############################################################################

from __future__ import absolute_import
from __future__ import division
from __future__ import unicode_literals

from mo_logs import Log

from mo_threads import MAIN_THREAD, Thread, Till


def timeout(please_stop):
    Log.note("begin waiting")
    (Till(seconds=10) | please_stop).wait()
    if please_stop:
        Log.note("EXIT DETECTED")
    else:
        Log.note("timeout detected")

Log.start(settings={"trace": True})

t = Thread.run("timeout", target=timeout)
t.release()

try:
    MAIN_THREAD.wait_for_shutdown_signal(allow_exit=False, wait_forever=False)
except Exception as e:
    Log.error("can not wait", cause=e)
