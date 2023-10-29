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


import math
from time import time as unix_now

from mo_logs import logger

from mo_threads import Thread, Till, wait_for_shutdown_signal, stop_main_thread


def timeout(please_stop):
    logger.info("begin waiting")
    end_time = unix_now() + 10
    done_waiting = Till(till=end_time) | please_stop
    while not done_waiting:
        Till(seconds=1).wait()
        logger.info("{remaining}", remaining=math.ceil(end_time-unix_now()))
    if please_stop:
        logger.info("EXIT DETECTED")
    else:
        logger.info("timeout detected")


try:
    logger.start(settings={"trace": True})
    Thread.run("timeout", target=timeout).release()
    wait_for_shutdown_signal(allow_exit=True, wait_forever=False)
except Exception as cause:
    logger.error("can not wait", cause=cause)
finally:
    stop_main_thread()
