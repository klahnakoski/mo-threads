# encoding: utf-8
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Contact: Kyle Lahnakoski (kyle@lahnakoski.com)
#


import os
import platform
import subprocess
from _thread import allocate_lock
from dataclasses import dataclass
from time import time as unix_now

from mo_dots import set_default, Null, Data
from mo_logs import logger, strings
from mo_logs.exceptions import Except
from mo_times import Timer

from mo_threads import threads
from mo_threads.lock import Lock
from mo_threads.queues import Queue
from mo_threads.signals import Signal
from mo_threads.threads import THREAD_STOP, Thread
from mo_threads.till import Till

DEBUG_PROCESS = False
DEBUG_COMMAND = False

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
        try:
            self.please_stop.go()
            # MAYBE "exit" WORKS?
            self.stdin.add("exit")
        except Exception:
            pass
        return self

    def join(self, till=None, raise_on_error=False):
        self.stopped.wait()
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
            if raise_on_error:
                logger.error(
                    "{process} FAIL: returncode={code}\n{stderr}",
                    process=self.name,
                    code=self.service.returncode,
                    stderr=list(self.stderr),
                )
            else:
                logger.warning(
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
                    self.service.wait(timeout=timeout)
                    break
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
        self.debug and logger.info("{process} ({name} is reading)", name=name, process=self.name)
        try:
            while not please_stop and self.service.returncode is None:
                line = pipe.readline()  # THIS MAY NEVER RETURN
                self.debug and logger.info("got line: {line}", line=line)
                status.last_read = unix_now()
                if not line:
                    break
                line = line.decode("utf8").rstrip()
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
                "{process} (stdin): {line}", process=self.name, line=line.rstrip(),
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

PROMPT = "READY_FOR_MORE"


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


available_command_locker = Lock("cmd lock")
available_command = {}
stale_command_killer = None


class Command(object):
    """
    FASTER Process CLASS - OPENS A COMMAND_LINE APP (CMD on windows) AND KEEPS IT OPEN FOR MULTIPLE COMMANDS
    EACH WORKING DIRECTORY WILL HAVE ITS OWN PROCESS, MULTIPLE PROCESSES WILL OPEN FOR THE SAME DIR IF MULTIPLE
    THREADS ARE REQUESTING Commands
    """

    def __init__(
        self, name, params, cwd=None, env=None, debug=False, shell=True, max_stdout=1024, bufsize=-1,
    ):
        global stale_command_killer

        self.name = name
        self.params = params
        self.key = (
            cwd,
            Data(**(env or {})),  # env WILL BE UPDATED BY CALLEE
            debug,
            shell,
        )
        self.stdout = Queue("stdout for " + name, max=max_stdout)
        self.stderr = Queue("stderr for " + name, max=max_stdout)
        self.process = None
        with available_command_locker:
            avail = available_command.setdefault(self.key, [])
            while avail:
                self.process, last_used = avail.pop()
                if self.process.stopped:
                    continue
                DEBUG_COMMAND and logger.info(
                    "Reuse process {process} for {command}", process=self.process.name, command=name,
                )

        if not self.process:
            self.process = Process(
                name="command shell",
                params=[cmd()],
                cwd=os_path(cwd),
                env=env,
                debug=debug,
                shell=shell,
                bufsize=bufsize,
                timeout=60 * 60,
                parent_thread=threads.MAIN_THREAD,
            )
            self.process.stdin.add(set_prompt())
            self.process.stdin.add(LAST_RETURN_CODE)
            DEBUG_COMMAND and logger.info(
                "New process {process} for {command}", process=self.process.name, command=name,
            )
            _wait_for_start(self.process.stdout, Null)

        self.process.stdin.add(" ".join(cmd_escape(p) for p in params))
        self.process.stdin.add(LAST_RETURN_CODE)
        self.stdout_thread = Thread.run(
            name + " stdout", self._stream_relay, "stdout", self.process.stdout, self.stdout,
        )
        self.stderr_thread = Thread.run(
            name + " stderr", self._stream_relay, "stderr", self.process.stderr, self.stderr,
        )
        self.stderr_thread.stopped.then(self._cleanup)
        self.returncode = None

    def _cleanup(self):
        global stale_command_killer

        with available_command_locker:
            if any(self.process == p for p, _ in available_command[self.key]):
                logger.error("Not expected")
            available_command[self.key].append((self.process, unix_now()))
            if not stale_command_killer:
                stale_command_killer = Thread.run(
                    "remove stale Command objects", remove_stale_commands, parent_thread=threads.MAIN_THREAD
                )

    def join(self, raise_on_error=False, till=None):
        try:
            # WAIT FOR COMMAND LINE RESPONSE ON stdout
            self.stdout_thread.join(till=till)
            DEBUG_COMMAND and logger.info("stdout IS DONE {params}", params=self.params)
        except Exception as cause:
            logger.error("unexpected problem processing stdout", cause=cause)

        try:
            self.stderr_thread.please_stop.go()
            self.stderr_thread.join(till=till)
            DEBUG_COMMAND and logger.info("stderr IS DONE {params}", params=self.params)
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

    def _stream_relay(self, name, source, destination, please_stop=None):
        """
        :param source:
        :param destination:
        :param please_stop:
        :return:
        """
        prompt_count = 0
        prompt = PROMPT + ">"
        line_count = 0

        try:
            while not please_stop:
                value = source.pop(till=please_stop)
                if value is None:
                    continue
                elif value is THREAD_STOP:
                    DEBUG_COMMAND and logger.info("got thread stop")
                    return
                elif line_count == 0 and "is not recognized as an internal or external command" in value:
                    DEBUG_COMMAND and logger.info("exit with error")
                    logger.error("Problem with command: {desc}", desc=value)
                elif value.startswith(prompt):
                    if prompt_count:
                        DEBUG_COMMAND and logger.info("prompt located, clean finish")
                        # GET THE ERROR LEVEL
                        self.returncode = int(source.pop(till=please_stop))
                        return
                    else:
                        prompt_count += 1
                else:
                    line_count += 1
                    destination.add(value)
        finally:
            destination.add(THREAD_STOP)
        DEBUG_COMMAND and logger.info(
            "{name} done with {please_stop}", name=name, please_stop=bool(please_stop),
        )


def remove_stale_commands(please_stop):
    """
    REMOVE COMMANDS THAT HAVE NOT BEEN USED IN A WHILE
    """
    global stale_command_killer
    while not please_stop:
        Till(seconds=10).wait()
        now = unix_now()
        with available_command_locker:
            ac = list(available_command.items())

        for key, processes in ac:
            for process, last_used in processes:
                if process.stopped or now - last_used > 60:
                    DEBUG_COMMAND and logger.info(
                        "removing stale process {process} for {key}", process=process.name, key=key,
                    )
                    process.stop()
                    processes.remove((process, last_used))

        if not any(available_command.values()):
            stale_command_killer = None
            return


def _wait_for_start(source, destination):
    prompt = PROMPT + ">" + LAST_RETURN_CODE

    while True:
        value = source.pop()
        if value.startswith(prompt):
            # GET THE ERROR LEVEL
            line = source.pop()
            try:
                returncode = int(line)
            except Exception:
                logger.error("not an int ({line})", line=line)
            destination.add(THREAD_STOP)
            return
        destination.add(value)


def os_path(path):
    """
    :return: OS-specific path
    """
    if path == None:
        return None
    if os.sep == "/":
        return path
    return str(path).lstrip("/")
