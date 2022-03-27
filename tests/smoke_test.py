from mo_threads import Command, stop_main_thread

c = Command("test", ["python", "-c", "print('test')"]).join()
print(c.stdout.pop_all())

stop_main_thread()
