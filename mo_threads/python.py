# encoding: utf-8
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Contact: Kyle Lahnakoski (kyle@lahnakoski.com)
#


import os
import sys
from json import dumps as value2json, loads as json2value

from mo_dots import to_data, from_data
from mo_future import is_windows
from mo_logs import Except, logger
from mo_threads import Lock, Process, Signal, THREAD_STOP, Thread, DONE, python_worker

DEBUG = False


class Python(object):
    def __init__(self, name, config, parent_thread=None):
        python_exe = sys.executable
        config = to_data(config)
        if config.debug.logs:
            logger.error("not allowed to configure logging on other process")

        logger.info("begin process in dir={dir}", dir=os.getcwd())
        # WINDOWS REQUIRED shell, WHILE LINUX NOT
        shell = is_windows
        python_worker_file = os.path.abspath(python_worker.__file__)
        self.process = Process(
            name,
            [python_exe, "-u", python_worker_file],
            env={**os.environ, "PYTHONPATH": "."},
            debug=DEBUG,
            cwd=os.getcwd(),
            shell=shell,
        )
        self.process.stdin.add(value2json(from_data(
            config
            | {
                "debug": {"trace": True},
                "constants": {"mo_threads": {"signals": {"DEBUG": False}, "lock": {"DEBUG": False}}},
            }
        )))
        while True:
            line = self.process.stdout.pop()
            if line == THREAD_STOP:
                logger.error("problem starting python process: STOP detected on stdout")
            if line == '{"out":"ok"}':
                break
            logger.info("waiting to start python: {line}", line=line)
        self.lock = Lock("wait for response from " + name)
        self.stop_error = None
        self.done = DONE
        self.response = None
        self.error = None

        self.watch_stdout = Thread.run(f"watching stdout for {name}", self._watch_stdout)
        self.watch_stderr = Thread.run(f"watching stderr for {name}", self._watch_stderr)

    def _execute(self, command):
        while True:
            self.done.wait()
            with self.lock:
                if self.done:
                    self.done = Signal()
                    break

        self.response = None
        self.error = None
        self.process.stdin.add(value2json(command), force=True)
        self.done.wait()
        try:
            if self.error:
                logger.error("problem with process call", cause=Except(**self.error))
            else:
                return self.response
        finally:
            self.response = None
            self.error = None

    def _watch_stdout(self, please_stop):
        while not please_stop:
            line = self.process.stdout.pop(till=please_stop)
            DEBUG and logger.info("stdout got {line}", line=line)
            if line == THREAD_STOP:
                please_stop.go()
                break
            elif not line:
                continue

            try:
                data = to_data(json2value(line))
            except Exception:
                logger.info("non-json line: {line}", line=line)
                continue

            try:
                if "log" in data:
                    logger.main_log.write(**data.log)
                elif "out" in data:
                    self.response = data.out
                    self.done.go()
                elif "err" in data:
                    self.error = data.err
                    self.done.go()
            except Exception as cause:
                logger.error("unexpected problem", cause=cause)
        DEBUG and logger.info("stdout reader is done")

    def _watch_stderr(self, please_stop):
        while not please_stop:
            try:
                line = self.process.stderr.pop(till=please_stop)
                DEBUG and logger.info("stderr got {line}", line=line)
                if line is None or line == THREAD_STOP:
                    please_stop.go()
                    break
                logger.info(
                    "Error line from {name}({pid}): {line}", line=line, name=self.process.name, pid=self.process.pid,
                )
            except Exception as cause:
                logger.error("could not process line", cause=cause)
        DEBUG and logger.info("stderr reader is done")

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
                    logger.error("Not allowed to use both args and kwargs")
                return self._execute({item: args})
            else:
                return self._execute({item: kwargs})

        return output

    def stop(self):
        try:
            self._execute({"stop": {}})
            self.process.stop()
            self.watch_stdout.stop()
            self.watch_stderr.stop()
        except Exception as cause:
            self.stop_error = cause
        return self

    def join(self):
        if self.stop_error:
            logger.error("problem with stop", cause=self.stop_error)

        self.process.join()
        self.watch_stdout.join()
        self.watch_stderr.join()
        return self
