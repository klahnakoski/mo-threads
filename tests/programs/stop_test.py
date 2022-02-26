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

from __future__ import absolute_import, division, unicode_literals

from mo_logs import Log

from mo_threads import Signal, Thread, stop_main_thread, wait_for_shutdown_signal
from mo_threads.till import Till

please_stop = Signal()


def timeout(please_stop):
    (Till(seconds=1) | please_stop).wait()
    stop_main_thread()


Thread.run("timeout", target=timeout, please_stop=please_stop)

Log.note("test if sys.exit() will send TERM signal")
wait_for_shutdown_signal(allow_exit=False, wait_forever=True, please_stop=please_stop)
Log.note("EXIT DETECTED")
