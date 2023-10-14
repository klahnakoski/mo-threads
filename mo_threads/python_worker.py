# encoding: utf-8
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Contact: Kyle Lahnakoski (kyle@lahnakoski.com)
#
import os
from copy import copy

from mo_dots import is_list, to_data
from mo_dots import listwrap, coalesce
from mo_logs import logger, constants, Except
from mo_logs.log_usingNothing import StructuredLogger

from mo_threads import Signal
from mo_threads.threads import STDOUT, STDIN, STDERR, THREAD_STOP, stop_main_thread

try:
    from mo_json import value2json, json2value
except ImportError:
    from json import dumps as value2json
    from json import loads as json2value

context = copy(globals())
del context["copy"]

DEBUG = False
DONE = value2json({"out": {}}).encode("utf8") + b"\n"
please_stop = Signal()


def command_loop(local):
    STDOUT.write(b'{"out":"ok"}\n')
    STDOUT.flush()
    DEBUG and logger.info("python process running")

    while not please_stop:
        line = STDIN.readline().decode("utf8").strip()
        if not line:
            continue
        try:
            command = json2value(line)
            DEBUG and logger.info("got {command}", command=command)

            if "import" in command:
                dummy = {}
                if isinstance(command["import"], str):
                    line = "from " + command["import"] + " import *"
                else:
                    line = f"from {command['import']['from']} import " + ",".join(listwrap(command["import"]["vars"]))
                DEBUG and logger.info("exec {line}", line=line)
                exec(line, dummy, context)
                STDOUT.write(DONE)
            elif "ping" in command:
                STDOUT.write(DONE)
            elif "set" in command:
                for k, v in command.set.items():
                    context[k] = v
                STDOUT.write(DONE)
            elif "get" in command:
                STDOUT.write(
                    value2json({"out": coalesce(
                        local.get(command["get"]), context.get(command["get"])
                    )}).encode("utf8")
                )
                STDOUT.write(b"\n")
            elif "stop" in command:
                STDOUT.write(DONE)
                please_stop.go()
            elif "exec" in command:
                if not isinstance(command["exec"], str):
                    logger.error("exec expects only text")
                exec(command["exec"], context, local)
                STDOUT.write(DONE)
            else:
                for k, v in command.items():
                    if is_list(v):
                        exec(
                            f"_return = {k}(" + ",".join(map(value2json, v)) + ")", context, local,
                        )
                    else:
                        exec(
                            f"_return = {k}(" + ",".join(kk + "=" + value2json(vv) for kk, vv in v.items()) + ")",
                            context,
                            local,
                        )
                    STDOUT.write(value2json({"out": local["_return"]}).encode("utf8"))
                    STDOUT.write(b"\n")
        except Exception as cause:
            cause = Except.wrap(cause)
            STDOUT.write(value2json({"err": cause}).encode("utf8"))
            STDOUT.write(b"\n")
        finally:
            STDOUT.flush()
            STDERR.flush()


num_temps = 0


def temp_var():
    global num_temps
    try:
        return f"temp_var{num_temps}"
    finally:
        num_temps += 1


class RawLogger(StructuredLogger):
    def write(self, template, params):
        STDOUT.write(value2json({"log": {"template": template, "params": params}}).encode("utf8") + b"\n")


def start():
    try:
        # EXPECTING CONFIGURATION FROM PARENT
        line = STDIN.readline().decode("utf8")
        config = to_data(json2value(line))
        constants.set(config.constants)
        logger.start(config.debug)
        logger.set_logger(RawLogger())

        # ENSURE WE HAVE A PYTHONPATH SET
        python_path = os.environ.get("PYTHONPATH")
        if not python_path:
            os.environ["PYTHONPATH"] = "."

        command_loop({"config": config})
    except Exception as e:
        logger.error("problem staring worker", cause=e)
    finally:
        stop_main_thread()


if __name__ == "__main__":
    start()
