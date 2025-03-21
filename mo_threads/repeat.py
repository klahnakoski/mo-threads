# encoding: utf-8
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at https://www.mozilla.org/en-US/MPL/2.0/.
#
# Contact: Kyle Lahnakoski (kyle@lahnakoski.com)
#


from mo_dots import is_null
from mo_future import is_text
from mo_logs import logger
from mo_times import Duration, Date

from mo_threads import Till, Thread, MAIN_THREAD
from mo_threads.signals import Signal


class Repeat:
    def __init__(self, message="ping", every="second", start=None, until=None):
        if is_text(message):
            self.message = show_message(message)
        else:
            self.message = message

        self.every = Duration(every)

        if isinstance(until, Signal):
            self.please_stop = until
        elif is_null(until):
            self.please_stop = Signal()
        else:
            self.please_stop = Till(Duration(until).seconds)

        self.thread = None
        if start:
            self.thread = (
                Thread
                .run(
                    "repeat",
                    _repeat,
                    self.message,
                    self.every,
                    Date(start),
                    parent_thread=MAIN_THREAD,
                    please_stop=self.please_stop,
                )
                .release()
            )

    def __enter__(self):
        if self.thread:
            logger.error("Use as context manager or use start parameter, not both")
        self.thread = Thread.run(
            "repeat", _repeat, self.message, self.every, Date.now(), please_stop=self.please_stop,
        )

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.please_stop.go()
        self.thread.join()
        self.thread = None


def _repeat(message, every, start, please_stop):
    next_time = start
    while not please_stop:
        message()
        next_time = next_time + every
        (please_stop | Till(till=next_time.unix)).wait()


def show_message(message):
    def output():
        logger.info(message)

    return output
