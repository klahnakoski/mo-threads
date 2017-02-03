# encoding: utf-8
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Author: Kyle Lahnakoski (kyle@lahnakoski.com)
#
from __future__ import absolute_import
from __future__ import division
from __future__ import unicode_literals

import os
import subprocess

from mo_dots import set_default, unwrap, get_module
from mo_logs import Log
from mo_logs.exceptions import Except

from mo_threads.lock import Lock
from mo_threads.queues import Queue
from mo_threads.signal import Signal
from mo_threads.threads import Thread, THREAD_STOP

DEBUG = True

string2quote = get_module("mo_json").quote

class Process(object):
    def __init__(self, name, params, cwd=None, env=None, debug=False, shell=False, bufsize=-1):
        self.name = name
        self.service_stopped = Signal("stopped signal for " + string2quote(name))
        self.stdin = Queue("stdin for process " + string2quote(name), silent=True)
        self.stdout = Queue("stdout for process " + string2quote(name), silent=True)
        self.stderr = Queue("stderr for process " + string2quote(name), silent=True)

        try:
            self.debug = debug or DEBUG
            self.service = service = subprocess.Popen(
                params,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=bufsize,
                cwd=cwd,
                env=unwrap(set_default(env, os.environ)),
                shell=shell
            )

            self.stopper = Signal()
            self.stopper.on_go(self._kill)
            self.thread_locker = Lock()
            self.children = [
                Thread.run(self.name + " waiter", self._monitor, parent_thread=self),
                Thread.run(self.name + " stdin", self._writer, service.stdin, self.stdin, please_stop=self.stopper, parent_thread=self),
                Thread.run(self.name + " stdout", self._reader, service.stdout, self.stdout, please_stop=self.stopper, parent_thread=self),
                # Thread.run(self.name + " stderr", self._reader, service.stderr, self.stderr, please_stop=self.stopper, parent_thread=self),
            ]
        except Exception, e:
            Log.error("Can not call", e)

    def stop(self):
        self.stdin.add("exit")  # ONE MORE SEND
        self.stopper.go()
        self.stdin.add(THREAD_STOP)
        self.stdout.add(THREAD_STOP)
        self.stderr.add(THREAD_STOP)

    def join(self):
        self.service_stopped.wait()
        with self.thread_locker:
            child_threads, self.children = self.children, []
        for c in child_threads:
            c.join()

    def remove_child(self, child):
        with self.thread_locker:
            self.children.remove(child)

    @property
    def pid(self):
        return self.service.pid

    def _monitor(self, please_stop):
        self.service.wait()
        if self.debug:
            Log.alert("{{name}} stopped with returncode={{returncode}}", name=self.name, returncode=self.service.returncode)
        self.stdin.add(THREAD_STOP)
        self.stdout.add(THREAD_STOP)
        self.stderr.add(THREAD_STOP)
        self.service_stopped.go()

    def _reader(self, pipe, recieve, please_stop):
        try:
            while not please_stop:
                line = pipe.readline()
                if self.service.returncode is not None:
                    # GRAB A FEW MORE LINES
                    for i in range(100):
                        try:
                            line = pipe.readline()
                            if line:
                                recieve.add(line)
                                if self.debug:
                                    Log.note("FROM {{process}}: {{line}}", process=self.name, line=line.rstrip())
                        except Exception:
                            break
                    return

                recieve.add(line)
                if self.debug:
                    Log.note("FROM {{process}}: {{line}}", process=self.name, line=line.rstrip())
        finally:
            pipe.close()

    def _writer(self, pipe, send, please_stop):
        while not please_stop:
            line = send.pop()
            if line == THREAD_STOP:
                please_stop.go()
                break

            if line:
                if self.debug:
                    Log.note("TO   {{process}}: {{line}}", process=self.name, line=line.rstrip())
                pipe.write(line + b"\n")
        pipe.close()

    def _kill(self):
        try:
            self.service.kill()
        except Exception, e:
            ee = Except.wrap(e)
            if 'The operation completed successfully' in ee:
                return
            if 'No such process' in ee:
                return

            Log.warning("Failure to kill process {{process|quote}}", process=self.name, cause=ee)


