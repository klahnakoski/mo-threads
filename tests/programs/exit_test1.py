# encoding: utf-8
#
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Author: Kyle Lahnakoski (kyle@lahnakoski.com)
#

###############################################################################
# Intended to test exit behaviour from timeout, SIGINT (CTRL-C), or "exit"
###############################################################################

from __future__ import absolute_import
from __future__ import division
from __future__ import unicode_literals

from mo_logs import Log

import tests.programs as _
from mo_threads import MAIN_THREAD

keep_import = _

try:
    MAIN_THREAD.wait_for_shutdown_signal(allow_exit=True, wait_forever=False)
except Exception as e:
    Log.error("can not wait", cause=e)
