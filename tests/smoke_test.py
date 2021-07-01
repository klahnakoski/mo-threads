from mo_threads import Command, MAIN_THREAD

Command("test", ["echo"]).join()

MAIN_THREAD.stop()
