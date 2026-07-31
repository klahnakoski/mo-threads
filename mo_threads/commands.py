# encoding: utf-8
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at https://www.mozilla.org/en-US/MPL/2.0/.
#
# Contact: Kyle Lahnakoski (kyle@lahnakoski.com)
#
import os
from shlex import quote

from mo_future import is_windows
from mo_logs import logger

from mo_threads import threads
from mo_threads.processes import os_path, Process
from mo_threads.queues import Queue
from mo_threads.threads import PLEASE_STOP, Thread
from mo_threads.till import Till

DEBUG = False

COMMAND_TIMEOUT = 5
START_TIMEOUT = 60
END_OF_COMMAND_MARKER = "END-OF-COMMAND-MARKER"


class Command:
    """
    OPEN A COMMAND_LINE APP (CMD on windows), RUN ONE COMMAND, THEN SHUT IT DOWN
    THE SHELL IS GONE BY THE TIME join() RETURNS, SO cwd IS FREE TO BE DELETED
    """

    def __init__(
        self, name, params, *, cwd=None, env=None, debug=False, shell=True, timeout=None, max_stdout=1024, bufsize=-1,
    ):
        cwd = os_path(cwd or os.getcwd())
        command = " ".join(cmd_escape(p) for p in params)
        self.debug = debug = debug or DEBUG
        self.debug and logger.info("command: {command}", command=command)

        self.name = name
        self.params = params
        self.timeout = timeout or COMMAND_TIMEOUT
        self.returncode = None
        self.process = process = Process(
            name=f"shell {name}",
            params=[cmd()],
            cwd=cwd,
            env=env,
            debug=debug,
            shell=shell,
            bufsize=bufsize,
            timeout=START_TIMEOUT,
            # THE SHELL OUTLIVES THE THREAD THAT ASKED FOR IT, IT IS STOPPED BY _worker
            parent_thread=threads.MAIN_THREAD,
        )
        set_prompt(process.stdin)

        # WAIT FOR START, AND CONSUME THE SHELL BANNER, SO ONLY COMMAND OUTPUT IS RETURNED
        process.stdin.add(LAST_RETURN_CODE)
        start_timeout = Till(seconds=START_TIMEOUT)
        while not start_timeout:
            value = process.stdout.pop(till=start_timeout)
            if value is PLEASE_STOP:
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
        process.timeout = self.timeout

        self.stdout = Queue(f"stdout for {name}", max=max_stdout)
        self.stderr = Queue(f"stderr for {name}", max=max_stdout)
        self.stderr_thread = Thread.run(f"{name} stderr", _stderr_relay, process.stderr, self.stderr).release()
        # stdout_thread IS CONSIDERED THE LIFETIME OF THE COMMAND
        self.worker_thread = Thread.run(f"{name} worker", self._worker, process.stdout, self.stdout).release()
        self.process.stdin.add(command)
        self.process.stdin.add("")
        self.process.stdin.add(LAST_RETURN_CODE)

    def stop(self):
        """
        WE ARE DONE WITH THIS COMMAND

        THE SHELL IS STILL CLOSED DOWN, SO join() CAN STILL BLOCK: A SHELL BUSY
        WITH A COMMAND WILL NOT READ "exit" UNTIL THAT COMMAND IS DONE (OR UNTIL
        timeout DECIDES IT IS UNRESPONSIVE)
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
                elif value is PLEASE_STOP:
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
            destination.add(PLEASE_STOP)
            # self.process.stderr.add(PLEASE_STOP)
            self.stderr_thread.please_stop.go()
            self.stderr_thread.join()
            _stop_shell(self.process)
            self.debug and logger.info("command worker done")


def _stderr_relay(source, destination, please_stop=None):
    while not please_stop:
        value = source.pop(till=please_stop)
        if value is PLEASE_STOP:
            break
        if value:
            destination.add(value)
    for value in source.pop_all():
        if value and value is not PLEASE_STOP:
            destination.add(value)

    destination.add(PLEASE_STOP)


def _stop_shell(process):
    """
    ASK THE SHELL TO EXIT, AND WAIT FOR IT TO BE GONE
    """
    try:
        if not process.stopped:
            process.stdin.add("exit")
    except Exception:
        pass
    try:
        process.join(raise_on_error=True)
    except Exception:
        pass


def release_shells(cwd):
    """
    DEPRECATED - SHELLS ARE NO LONGER POOLED, SO THERE IS NOTHING TO RELEASE;
    A Command CLOSES ITS SHELL BEFORE join() RETURNS
    """
    return 0


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
