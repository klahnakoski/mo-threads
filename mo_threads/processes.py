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
from mo_future import is_windows
from mo_logs import logger, strings
from mo_logs.exceptions import Except
from mo_threads.queues import Queue
from mo_threads.signals import Signal
from mo_threads.threads import THREAD_STOP, Thread, EndOfThread, ALL_LOCK, ALL
from mo_threads.till import Till
from mo_times import Timer, Date

DEBUG = False

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

        self.debug = debug or DEBUG
        self.name = f"{name} ({self.process_id})"
        self.stopped = Signal("stopped signal for " + strings.quote(name))
        self.please_stop = Signal("please stop for " + strings.quote(name))
        self.second_last_stdin = None
        self.last_stdin = None
        self.stdin = Queue("stdin for process " + strings.quote(name), silent=not self.debug)
        self.stdout = Queue("stdout for process " + strings.quote(name), silent=not self.debug)
        self.stderr = Queue("stderr for process " + strings.quote(name), silent=not self.debug)
        self.timeout = timeout
        self.monitor_period = 0.5

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
                env={str(k): str(v) for k, v in {**os.environ, **(env or {})}.items()},
                shell=shell,
            )

            self.stdout_status = Status(unix_now() + startup_timeout)
            self.stderr_status = Status(unix_now() + startup_timeout)
            self.kill_once = self.kill
            self.children = (
                Thread(
                    self.name + " stdin",
                    self._writer,
                    service.stdin,
                    self.stdin,
                    please_stop=self.please_stop,
                    parent_thread=Null,
                ),
                Thread(
                    self.name + " stdout",
                    self._reader,
                    "stdout",
                    service.stdout,
                    self.stdout,
                    self.stdout_status,
                    please_stop=self.please_stop,
                    parent_thread=Null,
                    daemon=True,  # MIGHT LOCKUP, ONLY WAY TO KILL IT
                ),
                Thread(
                    self.name + " stderr",
                    self._reader,
                    "stderr",
                    service.stderr,
                    self.stderr,
                    self.stderr_status,
                    please_stop=self.please_stop,
                    parent_thread=Null,
                    daemon=True,  # MIGHT LOCKUP, ONLY WAY TO KILL IT
                ),
                Thread(self.name + " monitor", self._monitor, please_stop=self.please_stop, parent_thread=self),
            )
            for child in self.children:
                child.start()
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

    def add_child(self, child):
        pass

    def stop(self):
        self.please_stop.go()
        return self

    def join(self, till=None, raise_on_error=True):
        on_error = logger.error if raise_on_error else logger.warning
        self.stopped.wait(till=till)
        if not self.children:
            return
        _, _, _, monitor_thread = self.children
        monitor_thread.join(till=till)

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
                took = now - last_out
                if took > self.timeout:
                    self.kill_once()
                    logger.warning(
                        "{last_sent} for {name} last used {last_used} took over {timeout} seconds to respond",
                        last_sent=self.second_last_stdin,
                        last_used=Date(last_out).format(),
                        timeout=self.timeout,
                        name=self.name,
                    )
                    break
                try:
                    self.service.wait(timeout=self.monitor_period)
                    if self.service.returncode is not None:
                        break
                    self.debug and logger.info("{name} waiting for response", name=self.name)
                except Exception:
                    # TIMEOUT, CHECK FOR LIVELINESS
                    pass

        (stdin_thread, stdout_thread, stderr_thread, _), self.children = self.children, ()

        # stdout can lock up in windows, so do not wait too long
        wait_limit = Till(seconds=1)
        try:
            stdout_thread.join(till=wait_limit)
        except:
            # THREAD LOST ON PIPE.readline()
            self.stdout.close()
            stdout_thread.release()
            stdout_thread.end_of_thread = EndOfThread(None, None)
            with ALL_LOCK:
                if stdout_thread.ident in ALL:
                    del ALL[stdout_thread.ident]
            stdout_thread.stopped.go()

        try:
            stderr_thread.join(till=wait_limit)
        except:
            # THREAD LOST ON PIPE.readline()
            self.stderr.close()
            stderr_thread.release()
            stderr_thread.end_of_thread = EndOfThread(None, None)
            with ALL_LOCK:
                if stderr_thread.ident in ALL:
                    del ALL[stderr_thread.ident]
            stderr_thread.stopped.go()

        self.stdin.close()
        stdin_thread.join()

        self.stopped.go()
        self.debug and logger.info(
            "{process} STOP: returncode={returncode}", process=self.name, returncode=self.service.returncode,
        )

    def _reader(self, name, pipe, receive, status: Status, please_stop):
        """
        MOVE LINES FROM pipe TO receive QUEUE
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
            if not please_stop:
                logger.warning("premature read failure", cause=cause)
        finally:
            self.debug and logger.info("{name} closed", name=name)
            self.please_stop.go()
            receive.close()
            pipe.close()

    def _writer(self, pipe, send, please_stop):
        while not please_stop:
            line = send.pop(till=please_stop)
            if line is THREAD_STOP:
                please_stop.go()
                self.debug and logger.info("got THREAD_STOP")
                break
            elif line is None:
                continue
            self.second_last_stdin = self.last_stdin
            self.last_stdin = line
            self.debug and logger.info(
                "send line: {line}", process=self.name, line=line.rstrip(),
            )
            try:
                pipe.write(line.encode("utf8"))
                pipe.write(EOL)
                pipe.flush()
            except Exception as cause:
                # HAPPENS WHEN PROCESS IS DONE
                self.debug and logger.info("pipe closed")
                break
        self.debug and logger.info("writer closed")

    def kill(self):
        self.kill_once = Null
        try:
            if self.service.returncode is not None:
                return
            self.service.kill()
            logger.info("{process} was successfully terminated.", process=self.name, stack_depth=1)
        except Exception as cause:
            cause = Except.wrap(cause)
            if "The operation completed successfully" in cause:
                return
            if "No such process" in cause:
                return

            logger.warning(
                "Failure to kill process {process|quote}", process=self.name, cause=cause,
            )


if is_windows:
    EOL = b"\r\n"
else:
    EOL = b"\n"


def os_path(path):
    """
    :return: OS-specific path
    """
    if path == None:
        return None
    if os.sep == "/":
        return path
    return str(path).lstrip("/")
