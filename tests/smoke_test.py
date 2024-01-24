import sys

from mo_logs import logger

from mo_threads import Command

logger.start({"trace": True})

c = Command("test", [sys.executable, "-c", "print('test')"]).join()
print(c.stdout.pop_all())

exit()
