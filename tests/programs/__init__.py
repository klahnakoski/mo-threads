from mo_logs import Log

from mo_threads import Thread, Till


def timeout(please_stop):
    Log.note("begin waiting")
    (Till(seconds=20) | please_stop).wait()
    if please_stop:
        Log.note("EXIT DETECTED")
    else:
        Log.note("timeout detected")
