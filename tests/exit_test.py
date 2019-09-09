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

from mo_future import text_type
from mo_logs import Log

from mo_threads import Thread, Signal, MAIN_THREAD
from mo_threads.threads import STDOUT
from mo_threads.till import Till


def blame():
    Log.warning("Got signal to stop timeout")


STDOUT.write(b"make stop signal\n")


please_stop = Signal()
please_stop.then(blame)

STDOUT.write(b"make please_stop\n")


def timeout(please_stop):
    timer = Till(seconds=20)
    (timer | please_stop).wait()
    if timer:
        STDOUT.write(("timer value"+text_type(timer._go)+"\n").encode("utf8"))
        STDOUT.write(b"problem with timer\n")
    if please_stop:
        STDOUT.write(b"problem with stop\n")
    STDOUT.write(b"out of time\n")
    please_stop.go()

STDOUT.write(b"defined timeout\n")

if please_stop:
    STDOUT.write(b"stopped before thread\n")
Thread.run("timeout", target=timeout, please_stop=please_stop)
if please_stop:
    STDOUT.write(b"stopped after thread\n")

Log.note("you must type 'exit', and press Enter, or wait 20seconds")
MAIN_THREAD.wait_for_shutdown_signal(allow_exit=True, please_stop=please_stop)

if not please_stop:
    Log.note("exit detected")
else:
    Log.note("timeout detected")
please_stop.go()
