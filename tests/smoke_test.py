from mo_threads import Command, stop_main_thread

Command("test", ["python", "-c", "print('test')"]).join()

stop_main_thread()
