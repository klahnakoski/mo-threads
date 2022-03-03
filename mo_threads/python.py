# encoding: utf-8
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Contact: Kyle Lahnakoski (kyle@lahnakoski.com)
#
from __future__ import absolute_import, division, unicode_literals

import os
import platform
from json import dumps as value2json, loads as json2value

from mo_dots import to_data, from_data
from mo_logs import Except, Log

from mo_threads import Lock, Process, Signal, THREAD_STOP, Thread, DONE

PYTHON = "python"
DEBUG = True


class Python(object):
    def __init__(self, name, config, parent_thread=None):
        config = to_data(config)
        if config.debug.logs:
            Log.error("not allowed to configure logging on other process")

        Log.note("begin process")
        # WINDOWS REQUIRED shell, WHILE LINUX NOT
        shell = "windows" in platform.system().lower()
        self.process = Process(
            name,
            [PYTHON, "-u", f"mo_threads{os.sep}python_worker.py"],
            debug=DEBUG,
            cwd=os.getcwd(),
            shell=shell,
        )
        self.process.stdin.add(value2json(from_data(
            config | {"debug": {"trace": True}}
        )))
        status = self.process.stdout.pop()
        if status != '{"out":"ok"}':
            Log.error(
                "could not start python\n{{error|indent}}",
                error=self.process.stderr.pop_all()
                + [status]
                + self.process.stdin.pop_all(),
            )
        self.lock = Lock("wait for response from " + name)
        self.current_task = DONE
        self.current_response = None
        self.current_error = None

        self.watch_stdout = Thread.run(f"watching stdout for {name}", self._watch_stdout)
        self.watch_stderr = Thread.run(f"watching stderr for {name}", self._watch_stderr)

    def _execute(self, command):
        with self.lock:
            (self.current_task | self.process.service_stopped).wait()
            self.current_task = Signal()
            self.current_response = None
            self.current_error = None

            if self.process.service_stopped:
                Log.error("python is not running")
            self.process.stdin.add(value2json(command))
            (self.current_task | self.process.service_stopped).wait()

            try:
                if self.current_error:
                    Log.error(
                        "problem with process call", cause=Except(**self.current_error)
                    )
                else:
                    return self.current_response
            finally:
                self.current_task = DONE
                self.current_response = None
                self.current_error = None

    def _watch_stdout(self, please_stop):
        while not please_stop:
            line = self.process.stdout.pop(till=please_stop)
            DEBUG and Log.note("stdout got {{line}}", line=line)
            if line is None or line == THREAD_STOP:
                please_stop.go()
                break
            try:
                data = to_data(json2value(line))
                if "log" in data:
                    Log.main_log.write(*data.log)
                elif "out" in data:
                    self.current_response = data.out
                    self.current_task.go()
                elif "err" in data:
                    self.current_error = data.err
                    self.current_task.go()
            except Exception as cause:
                Log.note("non-json line: {{line}}", line=line)
        DEBUG and Log.note("stdout reader is done")

    def _watch_stderr(self, please_stop):
        while not please_stop:
            try:
                line = self.process.stderr.pop(till=please_stop)
                if line is None or line == THREAD_STOP:
                    please_stop.go()
                    break
                Log.note(
                    "Error line from {{name}}({{pid}}): {{line}}",
                    line=line,
                    name=self.process.name,
                    pid=self.process.pid,
                )
            except Exception as cause:
                Log.error("could not process line", cause=cause)

    def import_module(self, module_name, var_names=None):
        if var_names is None:
            self._execute({"import": module_name})
        else:
            self._execute({"import": {"from": module_name, "vars": var_names}})

    def set(self, var_name, value):
        self._execute({"set": {var_name, value}})

    def get(self, var_name):
        return self._execute({"get": var_name})

    def execute_script(self, script):
        return self._execute({"exec": script})

    def __getattr__(self, item):
        def output(*args, **kwargs):
            if len(args):
                if kwargs.keys():
                    Log.error("Not allowed to use both args and kwargs")
                return self._execute({item: args})
            else:
                return self._execute({item: kwargs})

        return output

    def stop(self):
        self._execute({"stop": {}})
        self.process.stop()
        self.watch_stdout.stop()
        self.watch_stderr.stop()
        return self

    def join(self):
        self.process.join()
        self.watch_stdout.join()
        self.watch_stderr.join()
        return self
