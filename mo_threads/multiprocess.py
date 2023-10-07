# encoding: utf-8
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Contact: Kyle Lahnakoski (kyle@lahnakoski.com)
#


import os
import subprocess
from _thread import allocate_lock
from dataclasses import dataclass
from time import time as unix_now

from mo_dots import set_default, Null
from mo_logs import logger, strings
from mo_logs.exceptions import Except
from mo_threads.queues import Queue
from mo_threads.signals import Signal
from mo_threads.threads import THREAD_STOP, Thread
from mo_threads.till import Till
from mo_times import Timer

DEBUG_PROCESS = False

next_process_id_locker = allocate_lock()
next_process_id = 0


@dataclass
class Status:
    last_read: float


class Process(object):
    def __init__(
        self,
        name,
        params,
        cwd=None,
        env=None,
        debug=False,
        shell=False,
        bufsize=-1,
        timeout=2.0,
        startup_timeout=10.0,
        parent_thread=None,
    ):
        """
        Spawns multiple threads to manage the stdin/stdout/stderr of the child process; communication is done
        via proper thread-safe queues of the same name.

        Since the process is managed and monitored by threads, the main thread is not blocked when the child process
        encounters problems

        :param name: name given to this process
        :param params: list of strings for program name and parameters
        :param cwd: current working directory
        :param env: enviroment variables
        :param debug: true to be verbose about stdin/stdout
        :param shell: true to run as command line
        :param bufsize: if you want to screw stuff up
        :param timeout: how long to wait for process stdout/stderr before we consider it dead
                        ensure your process emits lines to stay alive
        :param startup_timeout: since the process may take a while to start outputting, this is the wait time
                        for the first output
        """
        global next_process_id_locker, next_process_id
        with next_process_id_locker:
            self.process_id = next_process_id
            next_process_id += 1

        self.debug = debug or DEBUG_PROCESS
        self.name = f"{name} ({self.process_id})"
        self.stopped = Signal("stopped signal for " + strings.quote(name))
        self.please_stop = Signal("please stop for " + strings.quote(name))
        self.stdin = Queue("stdin for process " + strings.quote(name), silent=not self.debug)
        self.stdout = Queue("stdout for process " + strings.quote(name), silent=not self.debug)
        self.stderr = Queue("stderr for process " + strings.quote(name), silent=not self.debug)
        self.timeout = timeout
        self.monitor_period = 10 if self.debug else timeout

        try:
            if cwd == None:
                cwd = os.getcwd()
            else:
                cwd = str(cwd)

            command = [str(p) for p in params]
            self.debug and logger.info("command: {command}", command=command)
            self.service = service = subprocess.Popen(
                [str(p) for p in params],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=bufsize,
                cwd=cwd,
                env={str(k): str(v) for k, v in set_default(env, os.environ).items()},
                shell=shell,
            )

            self.stdout_status = Status(unix_now() + startup_timeout)
            self.stderr_status = Status(unix_now() + startup_timeout)
            self.children = (
                Thread.run(
                    self.name + " stdin",
                    self._writer,
                    service.stdin,
                    self.stdin,
                    please_stop=self.please_stop,
                    parent_thread=Null,
                ),
                Thread.run(
                    self.name + " stdout",
                    self._reader,
                    "stdout",
                    service.stdout,
                    self.stdout,
                    self.stdout_status,
                    please_stop=self.please_stop,
                    parent_thread=Null,
                ),
                Thread.run(
                    self.name + " stderr",
                    self._reader,
                    "stderr",
                    service.stderr,
                    self.stderr,
                    self.stderr_status,
                    please_stop=self.please_stop,
                    parent_thread=Null,
                ),
                Thread.run(
                    self.name + " waiter", self._monitor, please_stop=self.please_stop, parent_thread=self,
                ),
            )
        except Exception as cause:
            logger.error("Can not call  dir={cwd}", cwd=cwd, cause=cause)

        self.debug and logger.info(
            "{process} START: {command}", process=self.name, command=" ".join(map(strings.quote, params)),
        )
        if not parent_thread:
            parent_thread = Thread.current()
        self.parent_thread = parent_thread
        parent_thread.add_child(self)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.join(raise_on_error=True)

    def stop(self):
        self.please_stop.go()
        return self

    def join(self, till=None, raise_on_error=False):
        on_error = logger.error if raise_on_error else logger.warning
        self.stopped.wait(till=till)
        if not self.children:
            return
        try:
            stdin_thread, stdout_thread, stderr_thread, monitor_thread = self.children
            monitor_thread.join(till=till)
            stdin_thread.join(till=till)
            # stdout can lock up in windows, so do not wait too long
            wait_limit = Till(seconds=0.5) | till
            try:
                stdout_thread.join(till=wait_limit)
            except:
                pass
            try:
                stderr_thread.join(till=wait_limit)
            except:
                pass
            if stdout_thread.is_alive() or stderr_thread.is_alive():
                self._kill()
        finally:
            self.children = ()

        if self.returncode != 0:
            on_error(
                "{process} FAIL: returncode={code}\n{stderr}",
                process=self.name,
                code=self.service.returncode,
                stderr=list(self.stderr),
            )
        return self

    def remove_child(self, child):
        pass

    @property
    def pid(self):
        return self.service.pid

    @property
    def returncode(self):
        return self.service.returncode

    def _monitor(self, please_stop):
        with Timer(self.name, verbose=self.debug):
            while not please_stop:
                now = unix_now()
                last_out = max(self.stdout_status.last_read, self.stderr_status.last_read)
                timeout = last_out + self.timeout - now
                if timeout < 0:
                    self._kill()
                    if self.debug:
                        logger.warning("{name} took too long to respond", name=self.name)
                    break
                try:
                    self.service.wait(timeout=self.monitor_period)
                    DEBUG_PROCESS and logger.info("{name} waiting for response", name=self.name)
                except Exception:
                    # TIMEOUT, CHECK FOR LIVELINESS
                    pass
            self.stopped.go()
            self.stdin.close()
        self.debug and logger.info(
            "{process} STOP: returncode={returncode}", process=self.name, returncode=self.service.returncode,
        )

    def _reader(self, name, pipe, receive, status: Status, please_stop):
        """
        MOVE LINES fROM pipe TO receive QUEUE
        """
        self.debug and logger.info("is reading")
        try:
            while not please_stop and self.service.returncode is None:
                data = pipe.readline()  # THIS MAY NEVER RETURN
                status.last_read = unix_now()
                line = data.decode("utf8").rstrip()
                self.debug and logger.info("got line: {line}", line=line)
                if not data:
                    break
                receive.add(line)
        except Exception as cause:
            logger.warning("premature read failure", cause=cause)
        finally:
            self.debug and logger.info("{name} closed", name=name)
            receive.close()
            pipe.close()
            self.service.stderr.close()
            self.service.stdout.close()
            self.please_stop.go()

    def _writer(self, pipe, send, please_stop):
        while not please_stop:
            line = send.pop(till=please_stop)
            if line is THREAD_STOP:
                please_stop.go()
                break
            elif line is None:
                continue

            self.debug and logger.info(
                "send line: {line}", process=self.name, line=line.rstrip(),
            )
            try:
                pipe.write(line.encode("utf8"))
                pipe.write(b"\n")
                pipe.flush()
            except Exception as cause:
                # HAPPENS WHEN PROCESS IS DONE
                break
        self.debug and logger.info("writer closed")

    def _kill(self):
        try:
            self.service.kill()
            logger.info("{process} was successfully terminated.", process=self.name)
        except Exception as cause:
            cause = Except.wrap(cause)
            if "The operation completed successfully" in cause:
                return
            if "No such process" in cause:
                return

            logger.warning(
                "Failure to kill process {process|quote}", process=self.name, cause=cause,
            )


def os_path(path):
    """
    :return: OS-specific path
    """
    if path == None:
        return None
    if os.sep == "/":
        return path
    return str(path).lstrip("/")
