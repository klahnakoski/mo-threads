# encoding: utf-8
#
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at https://www.mozilla.org/en-US/MPL/2.0/.
#
# Author: Kyle Lahnakoski (kyle@lahnakoski.com)
#

###############################################################################
# Intended to test exit behaviour from timeout, SIGINT (CTRL-C), or "exit"
###############################################################################


from mo_logs import logger

from mo_threads import Thread, Till, wait_for_shutdown_signal


def timeout(please_stop):
    logger.info("timout waiting")
    (Till(seconds=20) | please_stop).wait()
    if please_stop:
        logger.info("EXIT DETECTED")
    else:
        logger.info("timeout detected")


Thread.run("timeout", target=timeout)

logger.info("you must type 'exit', and press Enter, or wait 20seconds")
try:
    wait_for_shutdown_signal(allow_exit=True, wait_forever=False)
except Exception as cause:
    logger.error("can not wait", cause=cause)
