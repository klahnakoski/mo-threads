import sys

from mo_threads import Command, stop_main_thread

c = Command("test", [sys.executable, "-c", "print('test')"]).join()
print(c.stdout.pop_all())

stop_main_thread()
