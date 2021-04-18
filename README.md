
# More Threads!


|Branch      |Status   |
|------------|---------|
|master      | [![Build Status](https://travis-ci.org/klahnakoski/mo-threads.svg?branch=master)](https://travis-ci.org/klahnakoski/mo-threads) |
|dev         | [![Build Status](https://travis-ci.org/klahnakoski/mo-threads.svg?branch=dev)](https://travis-ci.org/klahnakoski/mo-threads)  [![Coverage Status](https://coveralls.io/repos/github/klahnakoski/mo-threads/badge.svg?branch=dev)](https://coveralls.io/github/klahnakoski/mo-threads?branch=dev)  |

## Module `threads`

The main benefits over Python's threading library is:

1. **Multi-threaded queues do not use serialization** - Serialization is 
great in the general case, where you may also be communicating between 
processes, but it is a needless overhead for single-process multi-threading. 
It is left to the programmer to ensure the messages put on the queue are 
not changed, which is not ominous demand.
2. **Shutdown order is deterministic and explicit** - Python's threading 
library is missing strict conventions for controlled and orderly shutdown. 
Each thread can shutdown on its own terms, but is expected to do so expediently.
  * All threads are required to accept a `please_stop` signal; are 
  expected to test it in a timely manner; and expected to exit when signalled.
  * All threads have a parent - The parent is responsible for ensuring their 
  children get the `please_stop` signal, and are dead, before stopping 
  themselves. This responsibility is baked into the thread spawning process, 
  so you need not deal with it unless you want.
3. Uses [**Signals**](#signal-class) to simplify logical 
dependencies among multiple threads, events, and timeouts.
4. **Logging and Profiling is Integrated** - Logging and exception handling 
is seamlessly integrated: This means logs are centrally handled, and thread 
safe. Parent threads have access to uncaught child thread exceptions, and 
the cProfiler properly aggregates results from the multiple threads.


### What's it used for

A good amount of time is spent waiting for underlying C libraries and OS
services to respond to network and file access requests. Multiple
threads can make your code faster, despite the GIL, when dealing with those
requests. For example, by moving logging off the main thread, we can get
up to 15% increase in overall speed because we no longer have the main thread
waiting for disk writes or remote logging posts. Please note, this level of
speed improvement can only be realized if there is no serialization happening
at the multi-threaded queue.  

### Do not use Async

[Actors](http://en.wikipedia.org/wiki/Actor_model) are easier to reason about than [async tasks](https://docs.python.org/3/library/asyncio-task.html). Mixing regular methods and co-routines (with their `yield from` pollution) is dangerous because:

1. calling styles between synchronous and asynchronous methods can be easily confused
2. actors can use blocking methods, async can not
3. there is no way to manage resource priority with co-routines.
4. stack traces are lost with co-routines
5. async scope easily escapes lexical scope, which promotes bugs 

Python's async efforts are still immature; a re-invention of threading functionality by another name. Expect to experience a decade of problems that are already solved by threading; [here is an example](https://www.python.org/dev/peps/pep-0550/).  

**Reading**

* Fibers were an async experiment using a stack, as opposed to the state-machine-based async Python uses now. It does not apply to my argument, but is an interesting read: [[Fibers are] not an appropriate solution for writing scalable concurrent software](http://www.open-std.org/JTC1/SC22/WG21/docs/papers/2018/p1364r0.pdf)


## Usage

Most threads will be declared and run in a single line. It is much like Python's threading library, except it demands a name for the thread: 

    thread = Thread.run("name", function, p1, p2, ...)
    
Sometimes you want to separate creation from starting:

    thread = Thread("name", function, p1, p2, ...)
    thread.start()
    
### `join()` vs `release()`

Once a thread is created, a few actions can be performed.

* `join()` - Join on `thread` will make the caller thread wait until `thread` has stopped. Then, return the resulting value or to re-raise `thread`'s exception in the caller.

      result = thread.join()     # may raise exception

* `release()` - Will ignore any return value, and post any exception to logging. Tracking is still performed; released threads are still properly stopped.  You may still `join()` on a released thread, but you risk being too late: The thread will have already completed and logged it's failure.

      thread.release()     # release thread resources asap, when done
  
* `stopped.wait()` - Every thread has a `stopped` Signal, which can be used for coordination by other threads. This allows a thread to wait for another to be complete and then resume. No errors or return values are captured

      thread.stopped.wait()
  

### Registering Threads

Threads created without this module can call your code; You want to ensure these "alien" threads have finished their work, released the locks, and exited your code before stopping. If you register alien threads, then `mo-threads` will ensure the alien work is done for a clean stop. 

    def my_method():
        with RegisterThread():
            t = Thread.current()   # we can now use this library on this thread 
            print(t.name)          # a name is always given to the alien thread 


### Synchronization Primitives

There are three major aspects of a synchronization primitive:

* **Resource** - Monitors and locks can only be owned by one thread at a time
* **Binary** - The primitive has only two states
* **Irreversible** - The state of the primitive can only be set, or advanced, never reversed

The last, *irreversibility* is very useful, but ignored in many threading
libraries. The irreversibility allows us to model progression; and
we can allow threads to poll for progress, or be notified of progress. 

These three aspects can be combined to give us 8 synchronization primitives:

* `- - -` - Semaphore
* `- B -` - Event
* `R - -` - Monitor
* `R B -` - **[Lock](#lock-class)**
* `- - I` - Iterator/generator
* `- B I` - **[Signal](#signal-class)** (or Promise)
* `R - I` - Private Iterator 
* `R B I` - Private Signal (best implemented as `is_done` Boolean flag)

## `Lock` Class

Locks are identical to [threading monitors](https://en.wikipedia.org/wiki/Monitor_(synchronization)), except for two differences: 

1. The `wait()` method will **always acquire the lock before returning**. This is an important feature, it ensures every line inside a `with` block has lock acquisition, and is easier to reason about.
2. Exiting a lock via `__exit__()` will **always** signal a waiting thread to resume. This ensures no signals are missed, and every thread gets an opportunity to react to possible change.
3. `Lock` is **not reentrant**! This is a feature to ensure locks are not held for long periods of time.

**Example**
```python
lock = Lock()
while not please_stop:
    with lock:
        while not todo:
            lock.wait(seconds=1)
        # DO SOME WORK
```
In this example, we look for stuff `todo`, and if there is none, we wait for a second. During that time others can acquire the `lock` and add `todo` items. Upon releasing the the `lock`, our example code will immediately resume to see what's available, waiting again if nothing is found.


## `Signal` Class

[The `Signal` class](mo_threads/signals.py) is a binary semaphore that can be signalled only once; subsequent signals have no effect. It can be signalled by any thread; any thread can wait on a `Signal`; and once signalled, all waiting threads are unblocked, including all subsequent waiting threads. A Signal's current state can be accessed by any thread without blocking. `Signal` is used to model thread-safe state advancement. It initializes to `False`, and when signalled (with `go()`) becomes `True`. It can not be reversed.  

Signals are like a Promise, but more explicit 

|   Signal     |      Promise       | Python Event |
|:------------:|:------------------:|:------------:|
|   s.go()     |    s.resolve()     |    s.set()   |
| s.then(f)    |     s.then(m)      |              |
|  s.wait()    |      await s       |   s.wait()   |
|   s & t      | Promise.all(s, t)  |              |
| s &vert; t   | Promise.race(s, t) |              |


Here is simple worker that signals when work is done. It is assumed `do_work` is executed by some other thread.

```python
class Worker:
    def __init__(self):
        self.is_done = Signal()
  
    def do_work(self):
        # DO WORK
        self.is_done.go()
```

You can attach methods to a `Signal`, which will be run, just once, upon `go()`. If already signalled, then the method is run immediately.

```python
worker = Worker()
worker.is_done.then(lambda: print("done"))
```

You may also wait on a `Signal`, which will block the current thread until the `Signal` is a go

```python
worker.is_done.wait()
print("worker thread is done")
```

`Signals` are first class, they can be passed around and combined with other Signals. For example, using the `__or__` operator (`|`):  `either = lhs | rhs`; `either` will be triggered when `lhs` or `rhs` is triggered.

```python
def worker(please_stop):
    while not please_stop:
        #DO WORK 

user_cancel = get_user_cancel_signal()
worker(user_cancel | Till(seconds=360))
```

`Signal`s can also be combined using logical and (`&`):  `both = lhs & rhs`; `both` is triggered only when both `lhs` and `rhs` are triggered:

```python
(workerA.stopped & workerB.stopped).wait()
print("both threads are done")
```

### Differences from Python's `Event`

* `Signal` is not reversable, while `Event` has a `clear()` method
* `Signal` allows function chaining using the `then` method
* Complex signals can be composed from simple signals using boolean logic  



## `Till` Class

[The `Till` class](mo-threads/till.py) (short for "until") is a special `Signal` used to represent timeouts.  

```python
Till(seconds=20).wait()
Till(till=Date("21 Jan 2016").unix).wait()
```

Use `Till` rather than `sleep()` because you can combine `Till` objects with other `Signals`. 

**Beware that all `Till` objects will be triggered before expiry when the main thread is asked to shutdown**
