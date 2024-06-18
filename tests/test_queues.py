
from unittest import TestCase
from queue import Empty, Full
from mo_threads import Queue, Thread, Till, PLEASE_STOP


class TestQueue(TestCase):

    def test_get_no_wait(self):
        q = Queue("")
        q.put(1)
        self.assertEqual(q.get_nowait(), 1)
        self.assertTrue(q.empty())
        with self.assertRaises(Empty):
            q.get_nowait()

    def test_get_wait(self):
        q = Queue("")
        q.put(1)
        self.assertEqual(q.get(), 1)
        self.assertTrue(q.empty())
        with self.assertRaises(Empty):
            q.get(timeout=0.1)

    def test_put_nowait(self):
        q = Queue("",  max=2)
        q.put_nowait(1)
        q.put_nowait(2)
        q.put_nowait(3)
        self.assertEqual(q.get(), 1)
        self.assertEqual(q.get(), 2)
        self.assertEqual(q.get(), 3)

        self.assertTrue(q.empty())
        with self.assertRaises(Empty):
            q.get(timeout=0.1)

    def test_put_timeout(self):
        q = Queue("", max=1)
        q.put(1)
        with self.assertRaises(Full):
            q.put(2, timeout=0.1)

    def test_unique(self):
        q = Queue("", unique=True)
        q.add(1)
        q.add(1)
        self.assertEqual(len(q), 1)

    def test_closed(self):
        q = Queue("")

        def drain(please_stop):
            Till(seconds=0.1).wait()
            while not please_stop:
                result = q.pop()
                if result is PLEASE_STOP:
                    break

        q.add(1)
        q.close()
        drain_thread = Thread.run("drain", drain)
        q.join()
        self.assertEqual(len(q), 0)
        drain_thread.stop().join()

    def test_put_to_closed(self):
        q = Queue("")
        q.close()
        with self.assertRaises(Exception):
            q.put(1)

    def test_add_to_closed(self):
        q = Queue("")
        q.close()
        with self.assertRaises(Exception):
            q.add(1)

    def test_push_to_closed(self):
        q = Queue("")
        q.close()
        with self.assertRaises(Exception):
            q.push(1)

    def test_push(self):
        q = Queue("")
        q.push(1)
        self.assertEqual(q.pop(), 1)

    def test_push_all(self):
        q = Queue("")
        q.push_all([1, 2])
        self.assertEqual(q.pop(), 2)
        self.assertEqual(q.pop(), 1)

    def test_push_all_closed(self):
        q = Queue("")
        q.close()
        with self.assertRaises(Exception):
            q.push_all([1, 2])

    def test_push_to_closed_max(self):
        q = Queue("", max=1)
        q.add(1)
        Thread.run("drain", close_and_drain, q)
        with self.assertRaises(Exception):
            q.push(1)

    def test_push_all_closed_max(self):
        q = Queue("", max=1)
        q.add(1)
        Thread.run("drain", close_and_drain, q)
        with self.assertRaises(Exception):
            q.push_all([1, 2])


def close_and_drain(q, please_stop):
    Till(seconds=0.1).wait()
    q.close()
    while not please_stop:
        result = q.pop()
        if result is PLEASE_STOP:
            break
