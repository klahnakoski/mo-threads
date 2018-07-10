
# More Threads!


## Module `threads`

The main benefits over Python's threading library is:

1. **Multi-threaded queues do not use serialization** - Serialization is 
great in the general case, where you may also be communicating between 
processes, but it is a needless overhead for single-process multi-threading. 
It is left to the programmer to ensure the messages put on the queue are 
not changed, which is not ominous demand.
2. **Shutdown order is deterministic and explicit** - Python's threading 
library is missing strict conventions for controlled and orderly shutdown. 
These conventions eliminate the need for `interrupt()` and `abort()`, both of 
which are unstable idioms when using resources. Each thread can shutdown on 
its own terms, but is expected to do so expediently.
  * All threads are required to accept a `please_stop` signal; are 
  expected to test it in a timely manner; and expected to exit when signalled.
  * All threads have a parent - The parent is responsible for ensuring their 
  children get the `please_stop` signal, and are dead, before stopping 
  themselves. This responsibility is baked into the thread spawning process, 
  so you need not deal with it unless you want.
3. Uses [**Signals**](#the-signal-and-till-classes) to simplify logical 
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

### Asynch vs. Actors

My personal belief is that [actors](http://en.wikipedia.org/wiki/Actor_model)
are easier to reason about than [asynch tasks](https://docs.python.org/3/library/asyncio-task.html).
Mixing regular methods and co-routines (with their `yield from` pollution) is
dangerous because:

1. calling styles between methods and co-routines can be easily confused
2. actors can use blocking methods, co-routines can not
3. there is no way to manage resource priority with co-routines.
4. stack traces are lost with co-routines
5. asynch scope easily escapes lexical scope, which promotes bugs 

Python's asynch efforts are a still-immature re-invention of threading functionality by another name. Expect to experience a decade of problems that are already solved by threading; [here is an example](https://www.python.org/dev/peps/pep-0550/)

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
* `- B -` - Binary Semaphore
* `R - -` - Monitor
* `R B -` - **Lock**
* `- - I` - Iterator/generator
* `- B I` - **Signal**
* `R - I` - Private Iterator 
* `R B I` - Private Signal (best implemented as `is_done` Boolean flag)

## `Lock` Class

Locks are identical to [threading monitors](https://en.wikipedia.org/wiki/Monitor_(synchronization)), except for two differences: 

1. The `wait()` method will **always acquire the lock before returning**. This is an important feature, it ensures every line inside a `with` block has lock acquisition is easier to reason about.
2. Exiting a lock via `__exit__()` will **always** signal a waiting thread resume. This ensures no signals are missed, and every thread gets an opportunity to react to possible change.

```python
    lock = Lo
    while not please_stop:
        with lock:
            while not todo:
                lock.wait(seconds=1)
            # DO SOME WORK

```

In this example, we look for stuff `todo`, and if there is none, we wait for a second. During that time others can acquire the `lock` and add `todo` items. Upon releasing the the `lock`, our example code will immediately resume to see what's available, waiting again if nothing is found.


## `Signal` and `Till` Classes

[The `Signal` class](mo_threads/signal.py) is like a binary semaphore that can be signalled only once; it can be signalled by any thread; subsequent signals have no effect. Any thread can wait on a `Signal`; and once signalled, all waiting threads are unblocked, including all subsequent waiting threads. A Signal's current state can be accessed by any thread without blocking. `Signal` is used to model thread-safe state advancement. It initializes to `False`, and when signalled (with `go()`) becomes `True`. It can not be reversed.  

```python
is_done = Signal()
yield is_done  # give signal to another that wants to know when done
# DO WORK
is_done.go()
```

You can attach methods to a `Signal`, which will be run, just once, upon `go()`. If already signalled, then the method is run immediately.

```python
is_done = Signal()
is_done.on_go(lambda: print("done"))
return is_done
```

You may also wait on a `Signal`, which will block the current thread until the `Signal` is a go

```python
is_done = worker_thread.stopped
is_done.wait()
is_done = print("worker thread is done")
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

## `Till` Class

[The `Till` class](https://github.com/klahnakoski/pyLibrary/blob/dev/pyLibrary/thread/till.py) is used to represent timeouts. They can serve as a `sleep()` replacement: 

```python
Till(seconds=20).wait()
Till(till=Date("21 Jan 2016").unix).wait()
```

Actually, `Till` is better than `sleep()` because you can combine them with other `Signals`. 

Beware that all `Till` objects will be triggered before expiry when the main thread is asked to shutdown