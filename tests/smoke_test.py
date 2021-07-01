from mo_threads import Command, MAIN_THREAD

Command("test", ["python", "-c", "print('test')"]).join()

MAIN_THREAD.stop()
