# encoding: utf-8
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Contact: Kyle Lahnakoski (kyle@lahnakoski.com)
#
import json
import os
from shlex import quote
from time import time as unix_now

from mo_dots import Data, from_data, to_data
from mo_future import is_windows
from mo_json import value2json
from mo_logs import logger
from mo_threads import threads
from mo_threads.lock import Lock
from mo_threads.processes import os_path, Process
from mo_threads.queues import Queue
from mo_threads.signals import Signal
from mo_threads.threads import THREAD_STOP, Thread
from mo_threads.till import Till
from mo_times import Date, SECOND

DEBUG = False

STALE_MAX_AGE = 60
INUSE_TIMEOUT = 5
AVAIL_TIMEOUT = 60 * 60
START_TIMEOUT = 60
END_OF_COMMAND_MARKER = "END-OF-COMMAND-MARKER"

lifetime_manager_locker = Lock("cmd lock")
lifetime_manager = None


class Command(object):
    """
    FASTER Process CLASS - OPENS A COMMAND_LINE APP (CMD on windows) AND KEEPS IT OPEN FOR MULTIPLE COMMANDS
    EACH WORKING DIRECTORY WILL HAVE ITS OWN PROCESS, MULTIPLE PROCESSES WILL OPEN FOR THE SAME DIR IF MULTIPLE
    THREADS ARE REQUESTING Commands
    """

    def __init__(
        self, name, params, *, cwd=None, env=None, debug=False, shell=True, timeout=None, max_stdout=1024, bufsize=-1,
    ):
        global lifetime_manager

        cwd = os_path(cwd)
        env_ = Data(**(env or {}))
        command = " ".join(cmd_escape(p) for p in params)
        self.debug = debug = debug or DEBUG
        self.debug and logger.info("command: {command}", command=command)

        self.params = params
        self.key = (cwd, env_, debug, shell)
        self.timeout = timeout or INUSE_TIMEOUT
        self.returncode = None
        with lifetime_manager_locker:
            if not lifetime_manager:
                lifetime_manager = LifetimeManager()
            self.manager = lifetime_manager
        self.process = process = self.manager.get_or_create_process(
            params=params,
            bufsize=bufsize,
            cwd=cwd,
            debug=debug,
            env=env_,
            name=name,
            shell=shell,
            timeout=self.timeout,
        )
        if debug:
            name = f"{name} (using {process.name})"
        self.name = name
        self.stdout = Queue("stdout for " + name, max=max_stdout)
        self.stderr = Queue("stderr for " + name, max=max_stdout)
        self.stderr_thread = Thread.run(f"{name} stderr", _stderr_relay, process.stderr, self.stderr).release()
        # stdout_thread IS CONSIDERED THE LIFETIME OF THE COMMAND
        self.worker_thread = Thread.run(f"{name} worker", self._worker, process.stdout, self.stdout).release()
        self.process.stdin.add(command)
        self.process.stdin.add("")
        self.process.stdin.add(LAST_RETURN_CODE)

    def stop(self):
        """
        PROCESS MAY STILL BE RUNNING, BUT WE ARE DONE WITH IT
        """
        self.worker_thread.please_stop.go()

    def join(self, raise_on_error=False, till=None):
        # WAIT FOR COMMAND LINE RESPONSE ON stdout
        self.worker_thread.join(till=till)

        if raise_on_error and self.returncode != 0:
            logger.error(
                "{process} FAIL: returncode={code}\n{stderr}",
                process=self.name,
                code=self.returncode,
                stderr=list(self.stderr),
            )
        return self

    def _worker(self, source, destination, please_stop=None):
        """
        :param source:
        :param destination:
        :param please_stop:
        :return:
        """
        try:
            line_count = 0

            while not please_stop:
                value = source.pop(till=please_stop)
                if value is None:
                    continue
                elif value is THREAD_STOP:
                    self.debug and logger.info("got thread stop")
                    return
                elif line_count == 0 and "is not recognized as an internal or external command" in value:
                    self.debug and logger.info("exit with error")
                    logger.error("Problem with command: {desc}", desc=value)
                elif PROMPT and value.startswith(PROMPT):
                    # DO NOT RETURN WHAT WAS SENT
                    continue
                elif value.startswith(END_OF_COMMAND_MARKER):
                    # GET THE ERROR LEVEL
                    self.returncode = int(source.pop(till=please_stop))
                    self.debug and logger.info("prompt located, {code}, clean finish", code=self.returncode)
                    return
                else:
                    line_count += 1
                    destination.add(value)
        finally:
            destination.add(THREAD_STOP)
            # self.process.stderr.add(THREAD_STOP)
            self.stderr_thread.please_stop.go()
            self.stderr_thread.join()
            self.manager.return_process(self.process)
            self.debug and logger.info("command worker done")


def _stderr_relay(source, destination, please_stop=None):
    while not please_stop:
        value = source.pop(till=please_stop)
        if value is THREAD_STOP:
            break
        if value:
            destination.add(value)
    for value in source.pop_all():
        if value and value is not THREAD_STOP:
            destination.add(value)

    destination.add(THREAD_STOP)


class LifetimeManager:
    def __init__(self):
        global lifetime_manager
        DEBUG and logger.info("new manager")
        self.locker = Lock()
        self.avail_processes = []
        self.inuse_processes = []
        self.wakeup = Signal()
        self.worker_thread = Thread.run("lifetime manager", self._worker, parent_thread=threads.MAIN_THREAD).release()

    def get_or_create_process(self, *, params, bufsize, cwd, debug, env, name, shell, timeout):
        now = unix_now()
        cwd = os_path(cwd or os.getcwd())
        env = to_data(env)
        process_key = (cwd, env, debug, shell)
        with self.locker:
            for i, (key, process, last_used) in enumerate(self.avail_processes):
                if process_key != key or process.stopped:
                    continue
                del self.avail_processes[i]
                process.stdout_status.last_read = now
                process.timeout = timeout
                self.inuse_processes.append((key, process, now))
                DEBUG and logger.info(
                    "Reuse process {process} for {command} (key={key})", process=process.name, command=name, key=value2json(key)
                )
                return process

        process = Process(
            name=f"shell {cwd}",
            params=[cmd()],
            cwd=cwd,
            env=env,
            debug=debug,
            shell=shell,
            bufsize=bufsize,
            timeout=START_TIMEOUT,
            parent_thread=self.worker_thread,
        )
        with self.locker:
            self.inuse_processes.append((process_key, process, unix_now()))

        set_prompt(process.stdin)

        DEBUG and logger.info("New process {process} for {command}", process=process.name, command=name)

        # WAIT FOR START
        try:
            process.stdin.add("cd " + cmd_escape(cwd))
            process.stdin.add(LAST_RETURN_CODE)
            start_timeout = Till(seconds=START_TIMEOUT)
            while not start_timeout:
                value = process.stdout.pop(till=start_timeout)
                if value == THREAD_STOP:
                    process.kill_once()
                    process.join()
                    logger.error("Could not start command, stdout closed early")
                if value and value.startswith(END_OF_COMMAND_MARKER):
                    break
            process.stdout.pop(till=start_timeout)  # GET THE ERROR LEVEL
            if start_timeout:
                process.kill_once()
                process.join()
                logger.error(
                    "Command line did not start within {timeout} seconds: ({command})",
                    timeout=START_TIMEOUT,
                    command=params,
                )

            process.timeout = timeout
            return process
        except Exception as cause:
            self.return_process(process)
            raise cause

    def return_process(self, process):
        with self.locker:
            for i, (key, p, last_used) in enumerate(self.inuse_processes):
                if p is process:
                    DEBUG and logger.info("return process {process}", process=process.name)
                    del self.inuse_processes[i]
                    process.timeout = AVAIL_TIMEOUT
                    self.avail_processes.append((key, process, unix_now()))
                    self.wakeup.go()
                    break
            else:
                logger.error("process not found")

    def _stop_stale_processes(self, too_old):
        DEBUG and logger.info("stop stale processes")
        with self.locker:
            stale = []
            fresh = []
            for key, process, last_used in self.avail_processes:
                if process.stopped or too_old > last_used:
                    stale.append((key, process, last_used))
                else:
                    fresh.append((key, process, last_used))
            self.avail_processes[:] = fresh

        for _, process, _ in stale:
            try:
                if not process.stopped:
                    process.stdin.add("exit")
            except Exception:
                pass

        for _, process, _ in stale:
            try:
                process.join(raise_on_error=True)
            except Exception:
                pass

        if DEBUG and stale:
            for key, process, last_used in stale:
                logger.info(
                    "removed stale process {process} (key={key})",
                    process=process.name,
                    key=json.dumps(key, default=from_data),
                )
            for key, process, last_used in list(self.avail_processes):
                logger.info(
                    "remaining process {process} (age={age})",
                    process=process.name,
                    age=(Date.now() - Date(last_used)).floor(SECOND),
                )
            for key, process, last_used in list(self.inuse_processes):
                logger.info(
                    "inuse process {process} (age={age})",
                    process=process.name,
                    age=(Date.now() - Date(last_used)).floor(SECOND),
                )

    def _worker(self, please_stop):
        """
        REMOVE COMMANDS THAT HAVE NOT BEEN USED IN A WHILE
        """
        global lifetime_manager
        wakeup = self.wakeup
        while not please_stop:
            please_stop.wait(till=(wakeup | Till(seconds=10)))
            if please_stop:
                DEBUG and logger.info("got please_stop")
                break
            elif wakeup:
                DEBUG and logger.info("got wakeup")
            else:
                DEBUG and logger.info("time for next review")

            too_old = unix_now() - STALE_MAX_AGE
            self._stop_stale_processes(too_old)
            with lifetime_manager_locker:
                with self.locker:
                    if not self.inuse_processes and not self.avail_processes:
                        DEBUG and logger.info("lifetime manager to shutdown")
                        lifetime_manager = None
                        break
                    wakeup = self.wakeup = Signal()

        # wait for inuse to finish
        DEBUG and logger.info("got {num} inuse processes to stop", num=len(self.inuse_processes))
        while True:
            with self.locker:
                if not self.inuse_processes:
                    break
                if DEBUG:
                    _, process, _ = self.inuse_processes[0]
                wakeup = self.wakeup = Signal()
            DEBUG and logger.info("wait on process {name} to stop", name=process.name)
            wakeup.wait()

        with self.locker:
            avail_processes = list(self.avail_processes)
        DEBUG and logger.info("exit {num} available processes", num=len(avail_processes))
        for _, process, _ in avail_processes:
            if not process.stopped:
                process.stdin.add("exit")
        for _, process, _ in avail_processes:
            process.stopped.wait()
        self._stop_stale_processes(unix_now())
        DEBUG and logger.info("lifetime manager done")


if is_windows:

    def cmd_escape(value):
        if value.__class__.__name__ == "File":
            value = value.os_path
        if " " in value or '"' in value:
            return '"' + value.replace('"', '""') + '"'
        return value

    PROMPT = "******PROMPT******"
    LAST_RETURN_CODE = f"echo {END_OF_COMMAND_MARKER} & echo %errorlevel%"

    def set_prompt(stdin):
        stdin.add(f"set PROMPT={PROMPT}")

    def cmd():
        return "%windir%\\system32\\cmd.exe"

    def to_text(value):
        return value.decode("latin1")


else:

    def cmd_escape(value):
        if value.__class__.__name__ == "File":
            value = value.os_path
        return quote(value)

    PROMPT = None
    LAST_RETURN_CODE = f"echo {cmd_escape(END_OF_COMMAND_MARKER)};echo $?"

    def set_prompt(stdin):
        pass

    def cmd():
        return "bash"

    def to_text(value):
        return value.decode("latin1")
