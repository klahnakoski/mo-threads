# encoding: utf-8
# THIS FILE IS AUTOGENERATED!
from setuptools import setup
setup(
    author='Kyle Lahnakoski',
    author_email='kyle@lahnakoski.com',
    classifiers=["Programming Language :: Python :: 3.7","Programming Language :: Python :: 3.8","Programming Language :: Python :: 3.9","Development Status :: 4 - Beta","Topic :: Software Development :: Libraries","Topic :: Software Development :: Libraries :: Python Modules","License :: OSI Approved :: Mozilla Public License 2.0 (MPL 2.0)","Programming Language :: Python :: 3.10"],
    description='More Threads! Simpler and faster threading.',
    extras_require={"tests":["mo-future","mo-dots","mo-logs","mo-json","mo-testing","jx-python","psutil","objgraph","mo-files"]},
    include_package_data=True,
    install_requires=["mo-dots==9.368.23092","mo-future==7.340.23006","mo-logs==7.374.23120","mo-math==7.368.23092","mo-times==5.374.23120"],
    license='MPL 2.0',
    long_description='\n# More Threads!\n\n\n|Branch      |Status   |\n|------------|---------|\n|master      | [![Build Status](https://app.travis-ci.com/klahnakoski/mo-threads.svg?branch=master)](https://travis-ci.com/github/klahnakoski/mo-threads) |\n|dev         | [![Build Status](https://app.travis-ci.com/klahnakoski/mo-threads.svg?branch=dev)](https://travis-ci.com/github/klahnakoski/mo-threads)  [![Coverage Status](https://coveralls.io/repos/github/klahnakoski/mo-threads/badge.svg?branch=dev)](https://coveralls.io/github/klahnakoski/mo-threads?branch=dev) ← thread coverage is missing  |\n\n## Module `threads`\n\nThe main benefits over Python\'s threading library is:\n\n1. **Multi-threaded queues do not use serialization** - Serialization is \ngreat in the general case, where you may also be communicating between \nprocesses, but it is a needless overhead for single-process multi-threading. \nIt is left to the programmer to ensure the messages put on the queue are \nnot changed, which is not ominous demand.\n2. **Shutdown order is deterministic and explicit** - Python\'s threading \nlibrary is missing strict conventions for controlled and orderly shutdown. \nEach thread can shutdown on its own terms, but is expected to do so expediently.\n  * All threads are required to accept a `please_stop` signal; are \n  expected to test it in a timely manner; and expected to exit when signalled.\n  * All threads have a parent - The parent is responsible for ensuring their \n  children get the `please_stop` signal, and are dead, before stopping \n  themselves. This responsibility is baked into the thread spawning process, \n  so you need not deal with it unless you want.\n3. Uses [**Signals**](#signal-class) to simplify logical \ndependencies among multiple threads, events, and timeouts.\n4. **Logging and Profiling is Integrated** - Logging and exception handling \nis seamlessly integrated: This means logs are centrally handled, and thread \nsafe. Parent threads have access to uncaught child thread exceptions, and \nthe cProfiler properly aggregates results from the multiple threads.\n\n\n### What\'s it used for\n\nA good amount of time is spent waiting for underlying C libraries and OS\nservices to respond to network and file access requests. Multiple\nthreads can make your code faster, despite the GIL, when dealing with those\nrequests. For example, by moving logging off the main thread, we can get\nup to 15% increase in overall speed because we no longer have the main thread\nwaiting for disk writes or remote logging posts. Please note, this level of\nspeed improvement can only be realized if there is no serialization happening\nat the multi-threaded queue.  \n\n### Do not use Async\n\n[Actors](http://en.wikipedia.org/wiki/Actor_model) are easier to reason about than [async tasks](https://docs.python.org/3/library/asyncio-task.html). Mixing regular methods and co-routines (with their `yield from` pollution) is dangerous because:\n\n1. calling styles between synchronous and asynchronous methods can be easily confused\n2. actors can use blocking methods, async can not\n3. there is no way to manage resource priority with co-routines.\n4. stack traces are lost with co-routines\n5. async scope easily escapes lexical scope, which promotes bugs \n\nPython\'s async efforts are still immature; a re-invention of threading functionality by another name. Expect to experience a decade of problems that are already solved by threading; [here is an example](https://www.python.org/dev/peps/pep-0550/).  \n\n**Reading**\n\n* Fibers were an async experiment using a stack, as opposed to the state-machine-based async Python uses now. It does not apply to my argument, but is an interesting read: [[Fibers are] not an appropriate solution for writing scalable concurrent software](http://www.open-std.org/JTC1/SC22/WG21/docs/papers/2018/p1364r0.pdf)\n\n\n## Writing threaded functions\n\nAll threaded functions must accept a `please_stop` parameter, which is a `Signal` to indicate the desire to stop.  The function should do it\'s best to promptly return. \n\n#### Simple work loop\n\nFor threaded functions that perform small chunks of work in some loop; the chunks are small enough that they will complete soon enough. Simply check the `please_stop` signal at the start of each loop:\n\n    def worker(p1, p2, please_stop):\n        while not please_stop:\n            do_some_small_chunk_of_work(p1)\n            \n#### One-time work\n            \nSometimes, threads are launched to perform low priority, one-time work. You may not need to check the `please_stop` signal: \n    \n    def worker(p1, p2, please_stop):\n         do_some_work_and_exit(p1, p2)\n\n#### Passing signals to others\n         \nThere are many times a more complex `please_stop` checks are required. For example, we want to wait on an input queue for the next task.  Many of the methods in `mo-threads` accept `Signals` instead of timeouts so they return quickly when signalled. You may pass the `please_stop` signal to these methods, or make your own.  Be sure to check if the method returned because it is done, or it returned because it was signaled to stop early.\n         \n```python \ndef worker(source, please_stop):\n     while not please_stop:\n        data = source.pop(till=please_stop)\n        if please_stop:  # did pop() return because of please_stop?\n            return\n        chunk_of_work(data)\n```\n\n#### Combining signals\n            \nWork might be done on some regular interval: The threaded function will sleep for a period and perform some work. In these cases you can combine Signals and `wait()` on either of them:\n\n```python \ndef worker(please_stop):\n    while not please_stop:\n        next_event = Till(seconds=1000)\n        (next_event | please_stop).wait()\n        if please_stop:  # is wait done becasue of please_stop?\n            return\n        chunk_of_work()\n```\n\n## Spawning threads\n\nMost threads will be declared and run in a single line. It is much like Python\'s threading library, except it demands a name for the thread: \n\n    thread = Thread.run("name", function, p1, p2, ...)\n    \nSometimes you want to separate creation from starting:\n\n    thread = Thread("name", function, p1, p2, ...)\n    thread.start()\n    \n   \n### `join()` vs `release()`\n\nOnce a thread is created, a few actions can be performed with the thread object:\n\n* `join()` - Join on `thread` will make the caller thread wait until `thread` has stopped. Then, return the resulting value or to re-raise `thread`\'s exception in the caller.\n\n      result = thread.join()     # may raise exception\n\n* `release()` - Will ignore any return value, and post any exception to logging. Tracking is still performed; released threads are still properly stopped.  You may still `join()` to guarantee the caller will wait for thread completion, but you risk being too late: The thread may have already completed and logged it\'s failure.\n\n      thread.release()     # release thread resources asap, when done\n  \n* `stopped.wait()` - Every thread has a `stopped` Signal, which can be used for coordination by other threads. This allows a thread to wait for another to be complete and then resume. No errors or return values are captured\n\n      thread.stopped.wait()\n  \n### Registering Threads\n\nThreads created without this module can call your code; You want to ensure these "alien" threads have finished their work, released the locks, and exited your code before stopping. If you register alien threads, then `mo-threads` will ensure the alien work is done for a clean stop. \n\n    def my_method():\n        with RegisterThread():\n            t = Thread.current()   # we can now use this library on this thread \n            print(t.name)          # a name is always given to the alien thread \n\n\n## Synchronization Primitives\n\nThere are three major aspects of a synchronization primitive:\n\n* **Resource** - Monitors and locks can only be owned by one thread at a time\n* **Binary** - The primitive has only two states\n* **Irreversible** - The state of the primitive can only be set, or advanced, never reversed\n\nThe last, *irreversibility* is very useful, but ignored in many threading\nlibraries. The irreversibility allows us to model progression; and\nwe can allow threads to poll for progress, or be notified of progress. \n\nThese three aspects can be combined to give us 8 synchronization primitives:\n\n* `- - -` - Semaphore\n* `- B -` - Event\n* `R - -` - Monitor\n* `R B -` - **[Lock](#lock-class)**\n* `- - I` - Iterator/generator\n* `- B I` - **[Signal](#signal-class)** (or Promise)\n* `R - I` - Private Iterator \n* `R B I` - Private Signal (best implemented as `is_done` Boolean flag)\n\n## `Lock` Class\n\nLocks are identical to [threading monitors](https://en.wikipedia.org/wiki/Monitor_(synchronization)), except for two differences: \n\n1. The `wait()` method will **always acquire the lock before returning**. This is an important feature, it ensures every line inside a `with` block has lock acquisition, and is easier to reason about.\n2. Exiting a lock via `__exit__()` will **always** signal a waiting thread to resume. This ensures no signals are missed, and every thread gets an opportunity to react to possible change.\n3. `Lock` is **not reentrant**! This is a feature to ensure locks are not held for long periods of time.\n\n**Example**\n```python\nlock = Lock()\nwhile not please_stop:\n    with lock:\n        while not todo:\n            lock.wait(seconds=1)\n        # DO SOME WORK\n```\nIn this example, we look for stuff `todo`, and if there is none, we wait for a second. During that time others can acquire the `lock` and add `todo` items. Upon releasing the the `lock`, our example code will immediately resume to see what\'s available, waiting again if nothing is found.\n\n\n## `Signal` Class\n\n[The `Signal` class](mo_threads/signals.py) is a binary semaphore that can be signalled only once; subsequent signals have no effect.\n  * It can be signalled by any thread; \n  * any thread can wait on a `Signal`; and \n  * once signalled, all waiting threads are unblocked, including all subsequent waiting threads. \n  * A Signal\'s current state can be accessed by any thread without blocking.\n   \n`Signal` is used to model thread-safe state advancement. It initializes to `False`, and when signalled (with `go()`) becomes `True`. It can not be reversed.  \n\nSignals are like a Promise, but more explicit \n\n|   Signal     |      Promise       | Python Event |\n|:------------:|:------------------:|:------------:|\n|   s.go()     |    p.resolve()     |    e.set()   |\n| s.then(f)    |     p.then(m)      |              |\n|  s.wait()    |      await p       |   e.wait()   |\n|  bool(s)     |                    |  e.is_set()  |\n|   s & t      | Promise.all(p, q)  |              |\n| s &vert; t   | Promise.race(p, q) |              |\n\n\nHere is simple worker that signals when work is done. It is assumed `do_work` is executed by some other thread.\n\n```python\nclass Worker:\n    def __init__(self):\n        self.is_done = Signal()\n  \n    def do_work(self):\n        do_some_work()\n        self.is_done.go()\n```\n\nYou can attach methods to a `Signal`, which will be run, just once, upon `go()`. If already signalled, then the method is run immediately.\n\n```python\nworker = Worker()\nworker.is_done.then(lambda: print("done"))\n```\n\nYou may also wait on a `Signal`, which will block the current thread until the `Signal` is a go\n\n```python\nworker.is_done.wait()\nprint("worker thread is done")\n```\n\n`Signals` are first class, they can be passed around and combined with other Signals. For example, using the `__or__` operator (`|`):  `either = lhs | rhs`; `either` will be triggered when `lhs` or `rhs` is triggered.\n\n```python\ndef worker(please_stop):\n    while not please_stop:\n        #DO WORK \n\nuser_cancel = Signal("user cancel")\n...\nworker(user_cancel | Till(seconds=360))\n```\n\n`Signal`s can also be combined using logical and (`&`):  `both = lhs & rhs`; `both` is triggered only when both `lhs` and `rhs` are triggered:\n\n```python\n(workerA.stopped & workerB.stopped).wait()\nprint("both threads are done")\n```\n\n### Differences from Python\'s `Event`\n\n* `Signal` is not reversable, while `Event` has a `clear()` method\n* `Signal` allows function chaining using the `then` method\n* Complex signals can be composed from simple signals using boolean logic  \n\n\n\n## `Till` Class\n\n[The `Till` class](mo-threads/till.py) (short for "until") is a special `Signal` used to represent timeouts.  \n\n```python\nTill(seconds=20).wait()\nTill(till=Date("21 Jan 2016").unix).wait()\n```\n\nUse `Till` rather than `sleep()` because you can combine `Till` objects with other `Signals`. \n\n**Beware that all `Till` objects will be triggered before expiry when the main thread is asked to shutdown**\n\n\n## `Command` Class\n\nIf you find process creation is too slow, the `Command` class can be used to recycle existing processes.  It has the same interface as `Process`, yet it manages a `bash` (or `cmd.exe`) session for you in the background.\n\n ',
    long_description_content_type='text/markdown',
    name='mo-threads',
    packages=["mo_threads"],
    url='https://github.com/klahnakoski/mo-threads',
    version='5.374.23120',
    zip_safe=False
)