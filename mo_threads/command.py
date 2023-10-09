# encoding: utf-8
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Contact: Kyle Lahnakoski (kyle@lahnakoski.com)
#


import platform
from time import time as unix_now

from mo_dots import Data
from mo_logs import logger, strings

from mo_threads import threads
from mo_threads.lock import Lock
from mo_threads.multiprocess import Process, os_path
from mo_threads.queues import Queue
from mo_threads.threads import THREAD_STOP, Thread
from mo_threads.till import Till

DEBUG = True

STALE_CHECK_PERIOD = 10
STALE_MAX_AGE = 60
PROMPT = "READY_FOR_MORE"

locker = Lock("cmd lock")
avail_processes = []
inuse_processes = []
lifetime_management_thread = None


class Command(object):
    """
    FASTER Process CLASS - OPENS A COMMAND_LINE APP (CMD on windows) AND KEEPS IT OPEN FOR MULTIPLE COMMANDS
    EACH WORKING DIRECTORY WILL HAVE ITS OWN PROCESS, MULTIPLE PROCESSES WILL OPEN FOR THE SAME DIR IF MULTIPLE
    THREADS ARE REQUESTING Commands
    """

    def __init__(
        self, name, params, cwd=None, env=None, debug=False, shell=True, max_stdout=1024, bufsize=-1,
    ):
        cwd = os_path(cwd)

        self.name = name
        self.params = params
        self.key = (cwd, Data(**(env or {})), debug, shell)
        self.stdout = Queue("stdout for " + name, max=max_stdout)
        self.stderr = Queue("stderr for " + name, max=max_stdout)
        self.process = process = self.get_or_create_process(bufsize, cwd, debug, env, name, shell)
        self.returncode = None
        self.process.stdin.add(" ".join(cmd_escape(p) for p in params))
        self.process.stdin.add(LAST_RETURN_CODE)
        self.stdout_thread = Thread.run(f"{name} stdout", self._relay, "stdout", process.stdout, self.stdout)
        self.stderr_thread = Thread.run(f"{name} stderr", self._relay, "stderr", process.stderr, self.stderr)
        self.stderr_thread.stopped.then(self._return_process)

    def get_or_create_process(self, bufsize, cwd, debug, env, name, shell):
        global lifetime_management_thread
        with locker:
            for i, (directory, process, last_used) in enumerate(avail_processes):
                if self.key != directory or process.stopped:
                    continue
                del avail_processes[i]
                inuse_processes.append((directory, process, unix_now()))
                DEBUG and logger.info(
                    "Reuse process {process} for {command}", process=process.name, command=name,
                )
                return process

        with locker:
            if not lifetime_management_thread:
                lifetime_management_thread = Thread(
                    "lifetime management", lifetime_management, parent_thread=threads.MAIN_THREAD
                ).start()

            process = Process(
                name="command shell",
                params=[cmd()],
                cwd=cwd,
                env=env,
                debug=debug,
                shell=shell,
                bufsize=bufsize,
                timeout=60 * 60,
                parent_thread=lifetime_management_thread,
            )
            inuse_processes.append((self.key, process, unix_now()))

        process.stdin.add(set_prompt())
        process.stdin.add(LAST_RETURN_CODE)
        DEBUG and logger.info(
            "New process {process} for {command}", process=process.name, command=name,
        )

        # WAIT FOR START
        timeout = Till(seconds=5)
        prompt = PROMPT + ">" + LAST_RETURN_CODE
        while True:
            value = process.stdout.pop(till=timeout)
            if value and value.startswith(prompt):
                break
        process.stdout.pop(till=timeout)  # GET THE ERROR LEVEL
        if timeout:
            process.kill()
            process.join()
            logger.error("Command line did not start in time")
        return process

    def _return_process(self):
        with locker:
            for i, (key, process, last_used) in enumerate(inuse_processes):
                if process is self.process:
                    del inuse_processes[i]
                    avail_processes.append((key, process, unix_now()))
                    break

    def join(self, raise_on_error=False, till=None):
        try:
            # WAIT FOR COMMAND LINE RESPONSE ON stdout
            self.stdout_thread.join(till=till)
            DEBUG and logger.info("stdout IS DONE {params}", params=self.params)
        except Exception as cause:
            logger.error("unexpected problem processing stdout", cause=cause)

        try:
            self.stderr_thread.please_stop.go()
            self.stderr_thread.join(till=till)
            DEBUG and logger.info("stderr IS DONE {params}", params=self.params)
        except Exception as cause:
            logger.error("unexpected problem processing stderr", cause=cause)

        if raise_on_error and self.returncode != 0:
            logger.error(
                "{process} FAIL: returncode={code}\n{stderr}",
                process=self.name,
                code=self.returncode,
                stderr=list(self.stderr),
            )
        return self

    def _relay(self, name, source, destination, please_stop=None):
        """
        :param source:
        :param destination:
        :param please_stop:
        :return:
        """
        try:
            prompt_count = 0
            prompt = PROMPT + ">"
            line_count = 0

            while not please_stop:
                value = source.pop(till=please_stop)
                if value is None:
                    continue
                elif value is THREAD_STOP:
                    DEBUG and logger.info("got thread stop")
                    return
                elif line_count == 0 and "is not recognized as an internal or external command" in value:
                    DEBUG and logger.info("exit with error")
                    logger.error("Problem with command: {desc}", desc=value)
                elif value.startswith(prompt):
                    if prompt_count:
                        # GET THE ERROR LEVEL
                        self.returncode = int(source.pop(till=please_stop))
                        DEBUG and logger.info("prompt located, {code}, clean finish", code=self.returncode)
                        return
                    else:
                        prompt_count += 1
                else:
                    line_count += 1
                    destination.add(value)
        except Exception as cause:
            logger.warning("unexpected problem processing {name}", name=name, cause=cause)
        finally:
            destination.add(THREAD_STOP)
        DEBUG and logger.info(
            "{name} done with {please_stop}", name=name, please_stop=bool(please_stop),
        )


def _stop_stale_threads(too_old):
    with locker:
        stale = [
            (key, process, last_used)
            for key, process, last_used in avail_processes
            if process.stopped or too_old > last_used
        ]

    for key, process, last_used in stale:
        DEBUG and logger.info(
            "removing stale process {process} for {key}", process=process.name, key=key,
        )
        try:
            with locker:
                avail_processes.remove((key, process, last_used))
            if not process.stopped:
                process.stdin.add("exit")
            process.join()
        except Exception:
            pass


def lifetime_management(please_stop):
    """
    REMOVE COMMANDS THAT HAVE NOT BEEN USED IN A WHILE
    """
    global lifetime_management_thread
    while not please_stop:
        (Till(seconds=STALE_CHECK_PERIOD) | please_stop).wait()
        if please_stop:
            break
        now = unix_now()
        too_old = now - STALE_MAX_AGE
        _stop_stale_threads(too_old)
        with locker:
            if not avail_processes and not inuse_processes:
                lifetime_management_thread = None
                return

    # wait for in_use to finish
    while inuse_processes:
        Till(seconds=1).wait()

    _stop_stale_threads(unix_now())


WINDOWS_ESCAPE_DCT = {
    "%": "%%",
    "&": "^&",
    "\\": "^\\",
    "<": "^<",
    ">": "^>",
    "^": "^^",
    "|": "^|",
    "\t": "^\t",
    "\n": "^\n",
    "\r": "^\r",
    " ": "^ ",
}


def cmd_escape(value):
    if hasattr(value, "os_path"):  # File
        value = value.os_path
    quoted = strings.quote(value)

    if " " not in quoted and quoted == '"' + value + '"':
        # SIMPLE
        quoted = value

    return quoted


if "windows" in platform.system().lower():
    LAST_RETURN_CODE = "echo %errorlevel%"

    def set_prompt():
        return "prompt " + PROMPT + "$g"

    def cmd():
        return "%windir%\\system32\\cmd.exe"

    def to_text(value):
        return value.decode("latin1")


else:
    LAST_RETURN_CODE = "echo $?"

    def set_prompt():
        return "set prompt=" + cmd_escape(PROMPT + ">")

    def cmd():
        return "bash"

    def to_text(value):
        return value.decode("latin1")
