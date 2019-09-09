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

print("start imports")

from mo_future import text_type
from mo_logs import Log

print("import threads")

from mo_threads import Thread, Signal, MAIN_THREAD, Till, till


# def blame():
#     Log.warning("Got signal to stop timeout")
#
#
# print("make stop signal\n")
#
#
# please_stop = Signal()
# please_stop.then(blame)
#
# print("make please_stop\n")
#
#
# def timeout(please_stop):
#     print("set debug")
#     till.DEBUG=True
#     print("make timer")
#     timer = Till(seconds=20)
#     print("set blame")
#     timer.then(blame)
#     (timer | please_stop).wait()
#     if timer:
#         print("timer value: " + text_type(timer._go) + "\n")
#         print("problem with timer\n")
#     if please_stop:
#         print("problem with stop\n")
#     print("out of time\n")
#     please_stop.go()
#
# print("defined timeout\n")
#
# if please_stop:
#     print("stopped before thread\n")
# Thread.run("timeout", target=timeout, please_stop=please_stop)
# if please_stop:
#     print("stopped after thread\n")
#
# Log.note("you must type 'exit', and press Enter, or wait 20seconds")
# MAIN_THREAD.wait_for_shutdown_signal(allow_exit=True, please_stop=please_stop)
#
# if not please_stop:
#     Log.note("exit detected")
# else:
#     Log.note("timeout detected")
# please_stop.go()
