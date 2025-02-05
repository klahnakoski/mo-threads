# encoding: utf-8
#
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at https://www.mozilla.org/en-US/MPL/2.0/.
#
# Contact: Kyle Lahnakoski (kyle@lahnakoski.com)

from mo_threads.signals import Signal


class Future(object):
    """
    REPRESENT A VALUE THAT MAY NOT BE READY YET
    """

    __slots__ = ["is_ready", "value"]

    def __init__(self):
        self.is_ready = Signal()
        self.value = None

    def wait(self, till=None):
        """
        WAIT FOR VALUE
        :return: value that was assign()ed
        """
        (self.is_ready | till).wait()
        return self.value

    def assign(self, value):
        """
        PROVIDE A VALUE THE OTHERS MAY BE WAITING ON
        """
        self.value = value
        self.is_ready.go()
