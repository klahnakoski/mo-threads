# encoding: utf-8
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Contact: Kyle Lahnakoski (kyle@lahnakoski.com)
#


import cProfile
import pstats

from mo_logs import logger
from mo_times import Date

from mo_threads.profile_utils import stats2tab
from mo_threads.queues import Queue
from mo_threads.threads import ALL_LOCK, ALL, Thread

try:
    from mo_files import File
except ImportError:
    raise logger.error("please `pip install mo-files` to use profiling")


DEBUG = False
FILENAME = "profile.tab"
cprofiler_stats = None


class CProfiler(object):
    """
    cProfiler CONTEXT MANAGER WRAPPER

    Single threaded example:

        with CProfile():
            # some code
        write_profiles()

    """

    __slots__ = ["cprofiler"]

    def __init__(self):
        self.cprofiler = cProfile.Profile()

    def __enter__(self):
        global cprofiler_stats

        DEBUG and logger.info("starting cprofile")
        if not cprofiler_stats:
            enable_profilers()
        self.cprofiler.enable()

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.cprofiler.disable()
        cprofiler_stats.add(pstats.Stats(self.cprofiler))
        del self.cprofiler
        DEBUG and logger.info("done cprofile")

    def enable(self):
        return self.cprofiler.enable()

    def disable(self):
        return self.cprofiler.disable()


def enable_profilers(filename=None):
    global FILENAME
    global cprofiler_stats

    if cprofiler_stats is not None:
        return
    if filename:
        FILENAME = filename

    cprofiler_stats = Queue("cprofiler stats")

    current_thread = Thread.current()
    with ALL_LOCK:
        threads = list(ALL.values())
    for t in threads:
        t.cprofiler = CProfiler()
        if t is current_thread:
            DEBUG and logger.info("starting cprofile for thread {name}", name=t.name)
            t.cprofiler.__enter__()
        else:
            DEBUG and logger.info(
                "cprofiler not started for thread {name} (already running)", name=t.name,
            )


def write_profiles(main_thread_profile=None):
    if main_thread_profile:
        cprofiler_stats.add(pstats.Stats(main_thread_profile.cprofiler))
    stats = cprofiler_stats.pop_all()

    DEBUG and logger.info("aggregating {num} profile stats", num=len(stats))
    acc = stats[0]
    for s in stats[1:]:
        acc.add(s)

    tab = stats2tab(acc)

    stats_file = File(FILENAME).add_suffix(Date.now().format("_%Y%m%d_%H%M%S"))
    stats_file.write(tab)
    DEBUG and logger.info("profile written to {filename}", filename=stats_file.abs_path)
