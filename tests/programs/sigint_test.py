# encoding: utf-8
#
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at https://www.mozilla.org/en-US/MPL/2.0/.
#

###############################################################################
# Intended to test exit behaviour from timeout, SIGINT (CTRL-C), or "exit"
###############################################################################


from mo_logs import logger

from mo_threads import Thread, Till, wait_for_shutdown_signal


def timeout(please_stop):
    logger.info("begin waiting")
    (Till(seconds=10) | please_stop).wait()
    if please_stop:
        logger.info("EXIT DETECTED")
    else:
        logger.info("timeout detected")


logger.start(settings={"trace": True})

t = Thread.run("timeout", target=timeout)
t.release()

try:
    wait_for_shutdown_signal(allow_exit=False, wait_forever=False)
except Exception as cause:
    logger.error("can not wait", cause=cause)
