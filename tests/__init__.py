# encoding: utf-8
#
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Author: Kyle Lahnakoski (kyle@lahnakoski.com)
#

import os

from mo_dots import to_data
from mo_logs.strings import expand_template

IS_WINDOWS = os.name == "nt"


class StructuredLogger_usingList(object):
    def __init__(self):
        self.lines = []

    def write(self, template, params):
        self.lines.append(expand_template(template, params))

    def stop(self):
        self.lines.append("logger stopped")


class StructuredLogger_usingRaw(object):
    def __init__(self):
        self.lines = to_data([])

    def write(self, template, params):
        self.lines.append((template, params))

    def stop(self):
        self.lines.append("logger stopped")
