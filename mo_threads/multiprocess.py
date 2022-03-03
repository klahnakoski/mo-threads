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
import subprocess
from _thread import allocate_lock

from mo_dots import set_default, Null, Data, is_null
from mo_future import text
from mo_logs import Log, strings
from mo_logs.exceptions import Except
from mo_times import Timer

from mo_threads.lock import Lock
from mo_threads.queues import Queue
from mo_threads.signals import Signal
from mo_threads.threads import THREAD_STOP, Thread
from mo_threads.till import Till

DEBUG_PROCESS = False
DEBUG_COMMAND = False

next_process_id_locker = allocate_lock()
next_process_id = 0


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
        """
        global next_process_id_locker, next_process_id
        with next_process_id_locker:
            self.process_id = next_process_id
            next_process_id += 1

        self.debug = debug or DEBUG_PROCESS
        self.name = name + " (" + text(self.process_id) + ")"
        self.service_stopped = Signal("stopped signal for " + strings.quote(name))
        self.stdin = Queue(
            "stdin for process " + strings.quote(name), silent=not self.debug
        )
        self.stdout = Queue(
            "stdout for process " + strings.quote(name), silent=not self.debug
        )
        self.stderr = Queue(
            "stderr for process " + strings.quote(name), silent=not self.debug
        )

        try:
            if is_null(cwd):
                cwd = os.getcwd()
            else:
                cwd = str(cwd)

            command = [str(p) for p in params]
            self.debug and Log.note("command: {{command}}", command=command)
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

            self.please_stop = Signal()
            self.child_locker = Lock()
            self.children = [
                Thread.run(
                    self.name + " stdin",
                    self._writer,
                    service.stdin,
                    self.stdin,
                    please_stop=self.service_stopped,
                    parent_thread=self,
                ),
                Thread.run(
                    self.name + " stdout",
                    self._reader,
                    "stdout",
                    service.stdout,
                    self.stdout,
                    please_stop=self.service_stopped,
                    parent_thread=self,
                ),
                Thread.run(
                    self.name + " stderr",
                    self._reader,
                    "stderr",
                    service.stderr,
                    self.stderr,
                    please_stop=self.service_stopped,
                    parent_thread=self,
                ),
                Thread.run(self.name + " waiter", self._monitor, parent_thread=self),
            ]
        except Exception as cause:
            Log.error("Can not call", cause)

        self.debug and Log.note(
            "{{process}} START: {{command}}",
            process=self.name,
            command=" ".join(map(strings.quote, params)),
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
        self.stdin.add(THREAD_STOP)
        self.please_stop.go()
        return self

    def join(self, raise_on_error=False):
        self.service_stopped.wait()
        with self.child_locker:
            child_threads, self.children = self.children, []
        for c in child_threads:
            c.join()
        if self.returncode != 0:
            if raise_on_error:
                Log.error(
                    "{{process}} FAIL: returncode={{code}}\n{{stderr}}",
                    process=self.name,
                    code=self.service.returncode,
                    stderr=list(self.stderr),
                )
            else:
                Log.warning(
                    "{{process}} FAIL: returncode={{code}}\n{{stderr}}",
                    process=self.name,
                    code=self.service.returncode,
                    stderr=list(self.stderr),
                )
        return self

    def remove_child(self, child):
        with self.child_locker:
            try:
                self.children.remove(child)
            except Exception:
                pass

    @property
    def pid(self):
        return self.service.pid

    @property
    def returncode(self):
        return self.service.returncode

    def _monitor(self, please_stop):
        with Timer(self.name, verbose=self.debug):
            self.service.wait()
            self.debug and Log.note(
                "{{process}} STOP: returncode={{returncode}}",
                process=self.name,
                returncode=self.service.returncode,
            )
            self.service_stopped.go()
            please_stop.go()

    def _reader(self, name, pipe, receive, please_stop):
        try:
            while not please_stop and self.service.returncode is None:
                line = to_text(pipe.readline().rstrip())
                if line:
                    receive.add(line)
                    self.debug and Log.note(
                        "{{process}} ({{name}}): {{line}}",
                        name=name,
                        process=self.name,
                        line=line,
                    )
                else:
                    (Till(seconds=0.1) | please_stop).wait()

            # GRAB A FEW MORE LINES
            max = 100
            while max:
                try:
                    line = to_text(pipe.readline().rstrip())
                    if line:
                        max = 100
                        receive.add(line)
                        self.debug and Log.note(
                            "{{process}} RESIDUE: ({{name}}): {{line}}",
                            name=name,
                            process=self.name,
                            line=line,
                        )
                    else:
                        max -= 1
                except Exception:
                    break
        finally:
            pipe.close()
            receive.add(THREAD_STOP)
            self.debug and Log.note(
                "{{process}} ({{name}} is closed)", name=name, process=self.name
            )

        receive.add(THREAD_STOP)

    def _writer(self, pipe, send, please_stop):
        while not please_stop:
            line = send.pop(till=please_stop)
            if line is THREAD_STOP:
                please_stop.go()
                break
            elif line is None:
                continue

            self.debug and Log.note(
                "{{process}} (stdin): {{line}}", process=self.name, line=line.rstrip()
            )
            pipe.write(line.encode("utf8") + b"\n")
            pipe.flush()

    def _kill(self):
        try:
            self.service.kill()
            Log.note("{{process}} was successfully terminated.", process=self.name)
        except Exception as cause:
            cause = Except.wrap(cause)
            if "The operation completed successfully" in cause:
                return
            if "No such process" in cause:
                return

            Log.warning(
                "Failure to kill process {{process|quote}}",
                process=self.name,
                cause=cause,
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
    if hasattr(value, "abspath"):
        quoted = strings.quote(value.abspath)
    else:
        quoted = strings.quote(value)

    if quoted == '"' + value + '"':
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


class Command(object):
    """
    FASTER Process CLASS - OPENS A COMMAND_LINE APP (CMD on windows) AND KEEPS IT OPEN FOR MULTIPLE COMMANDS
    EACH WORKING DIRECTORY WILL HAVE ITS OWN PROCESS, MULTIPLE PROCESSES WILL OPEN FOR THE SAME DIR IF MULTIPLE
    THREADS ARE REQUESTING Commands
    """

    available_locker = Lock("cmd lock")
    available_process = {}

    def __init__(
        self,
        name,
        params,
        cwd=None,
        env=None,
        debug=False,
        shell=True,
        max_stdout=1024,
        bufsize=-1,
    ):

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
        with Command.available_locker:
            avail = Command.available_process.setdefault(self.key, [])
            if avail:
                self.process = avail.pop()
                DEBUG_COMMAND and Log.note(
                    "Reuse process {{process}} for {{command}}",
                    process=self.process.name,
                    command=name,
                )

        if not self.process:
            self.process = Process(
                "command shell", [cmd()], cwd, env, debug, shell, bufsize
            )
            self.process.stdin.add(set_prompt())
            self.process.stdin.add(LAST_RETURN_CODE)
            DEBUG_COMMAND and Log.note(
                "New process {{process}} for {{command}}",
                process=self.process.name,
                command=name,
            )
            _wait_for_start(self.process.stdout, Null)

        self.process.stdin.add(" ".join(cmd_escape(p) for p in params))
        self.process.stdin.add(LAST_RETURN_CODE)
        self.stdout_thread = Thread.run(
            name + " stdout",
            self._stream_relay,
            "stdout",
            self.process.stdout,
            self.stdout,
        )
        self.stderr_thread = Thread.run(
            name + " stderr",
            self._stream_relay,
            "stderr",
            self.process.stderr,
            self.stderr,
        )
        self.stderr_thread.stopped.then(self._cleanup)
        self.returncode = None

    def _cleanup(self):
        with Command.available_locker:
            if any(self.process == p for p in Command.available_process[self.key]):
                Log.error("Not expected")
            Command.available_process[self.key].append(self.process)

    def join(self, raise_on_error=False, till=None):
        try:
            # WAIT FOR COMMAND LINE RESPONSE ON stdout
            self.stdout_thread.join(till=till)
            DEBUG_COMMAND and Log.note("stdout IS DONE {{params}}", params=self.params)
        except Exception as cause:
            Log.error("unexpected problem processing stdout", cause=cause)

        try:
            self.stderr_thread.please_stop.go()
            self.stderr_thread.join(till=till)
            DEBUG_COMMAND and Log.note("stderr IS DONE {{params}}", params=self.params)
        except Exception as cause:
            Log.error("unexpected problem processing stderr", cause=cause)

        if raise_on_error and self.returncode != 0:
            Log.error(
                "{{process}} FAIL: returncode={{code}}\n{{stderr}}",
                process=self.name,
                code=self.returncode,
                stderr=list(self.stderr),
            )
        return self

    def _stream_relay(self, name, source, destination, please_stop=None):
        """
        :param source:
        :param destination:
        :param error: Throw error if line shows up
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
                    DEBUG_COMMAND and Log.note("got thread stop")
                    return
                elif (
                    line_count == 0
                    and "is not recognized as an internal or external command" in value
                ):
                    DEBUG_COMMAND and Log.note("exit with error")
                    Log.error("Problem with command: {{desc}}", desc=value)
                elif value.startswith(prompt):
                    if prompt_count:
                        DEBUG_COMMAND and Log.note("prompt located, clean finish")
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
        DEBUG_COMMAND and Log.note(
            "{{name}} done with {{please_stop}}",
            name=name,
            please_stop=bool(please_stop),
        )


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
                Log.error("not an int ({{line}})", line=line)
            destination.add(THREAD_STOP)
            return
        destination.add(value)
