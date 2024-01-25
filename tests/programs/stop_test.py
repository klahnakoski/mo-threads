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


from mo_logs import logger

from mo_threads import Signal, Thread, stop_main_thread, wait_for_shutdown_signal
from mo_threads.till import Till

please_stop = Signal()


def timeout(please_stop):
    logger.info("starting")
    (Till(seconds=5) | please_stop).wait()
    logger.info("time up")
    stop_main_thread()
    logger.info("done")


Thread.run("timeout", target=timeout, please_stop=please_stop)

logger.info("test if sys.exit() will send TERM signal")
wait_for_shutdown_signal(allow_exit=False, wait_forever=True, please_stop=please_stop)
logger.info("EXIT DETECTED")
