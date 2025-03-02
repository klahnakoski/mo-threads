from mo_threads.queues import Queue
from mo_threads.threads import Thread
from mo_threads.threads import join_all_threads, PLEASE_STOP


class ThreadPool:
    def __init__(self, num_threads, name=None):
        self.name = name or "Pool"
        self.num_threads = num_threads
        self.workers = []
        self.queue = Queue(f"todo list for {name}")

    def __enter__(self):
        self.workers = [Thread.run(f"{self.name}-worker-{i}", worker, self) for i in range(self.num_threads)]
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.queue.close()
        self.results = join_all_threads(self.workers)

    def run(self, name, target, *args, **kwargs):
        thread = Thread(name, target, *args, **kwargs)
        self.queue.add(thread)
        return thread


def worker(pool, please_stop):
    while not please_stop:
        thread = pool.queue.pop(till=please_stop)
        if thread is PLEASE_STOP:
            break
        try:
            thread.start().join(till=please_stop)
        except Exception:
            pass
