# mo_threads — known defects

Found while modernising `mo-deploy` on 2026-07-30. Items 1 and 3 concerned the `Command`
shell pool, which was removed on 2026-07-31 — both are closed by that removal. Item 2 is
fixed in mo-deploy's vendored copy and awaits an svn publish.

---

## 0. The `Command` shell pool is gone (2026-07-31)

`Command` used to keep a shell per `(cwd, env, debug, shell)` in a `LifetimeManager`, hand it
out to the next matching `Command`, and park it for `AVAIL_TIMEOUT` (one hour) in between.
Now each `Command` opens its own shell and shuts it down in `_worker`'s `finally`, so by the
time `join()` returns the shell is gone and its `cwd` is free.

Deleted: `LifetimeManager`, `lifetime_manager`, `lifetime_manager_locker`, `AVAIL_TIMEOUT`,
`STALE_MAX_AGE`, and `INUSE_TIMEOUT` (renamed `COMMAND_TIMEOUT`, since there is no longer an
"in use" state). `release_shells(cwd)` remains as a no-op returning `0` so mo-deploy keeps
importing — its calls can be deleted whenever mo-deploy next syncs.

**The cost is much smaller than the serial number suggests.** Measured 2026-07-31,
Windows 11:

| | per command |
|---|---|
| pooled shell (reused, before) | 2 ms |
| own shell, 100 in parallel (now) | **7-9 ms** |
| own shell, one at a time (now) | 35 ms |
| plain `subprocess.run(shell=True)`, serial | 14 ms |

Serially the 35 ms breaks down as: 4 ms `Popen`, **28 ms for cmd.exe to boot to its first
prompt**, 1 ms to run the command, 6 ms teardown. That 28 ms is almost all *waiting* on
cmd.exe, not CPU, so it overlaps: 100 shells opened, used and closed at once finish in
0.7-0.9 s wall clock, 7-9 ms each, stable across runs and leaking no `cmd.exe`
(`tests/test_processes.py::TestShellLifetime::test_many_shells_in_parallel`). Against the
2 ms pooled figure that is a 5-7 ms penalty for a concurrent caller, not 33 ms.

There is no cheap win left in `commands.py` for the serial case — folding the startup
handshake into the worker thread would only overlap that 28 ms with the caller's next work,
not remove it. A caller that issues thousands of commands one after another and cannot run
them concurrently should pool in its own code, where it can scope a shell's lifetime to work
it actually controls.

`Command.stop()` still shuts the shell down, but a shell busy with a command cannot read
`exit` until that command finishes, so `join()` after `stop()` blocks until then (or until
`timeout` has the monitor kill the shell). Covered by
`tests/test_processes.py::TestShellLifetime`.

---

## 1. A pooled shell holds its `cwd`, so the directory cannot be deleted (CLOSED — no pool, no held `cwd`)

`Command._worker` used to call `return_process`, stamping `AVAIL_TIMEOUT` (one hour) on the
shell and parking it in `avail_processes` with `cwd` still as its working directory —
and **Windows will not let anyone remove a directory a live process is sitting in**.

Measured at the time: a `mo_files.TempDirectory` used as a `Command` cwd still existed 12s
after its `with` block exited, while an otherwise identical unused one was gone.
`mo-deploy`'s `Module.run_tests` allocates two per python version per module — a virtualenv
and a git worktree — and every one of them survived the deploy.

`release_shells(cwd)` (svn r2915, 2026-07-30) was the workaround: it evicted idle shells
sitting in `cwd` so callers could get their own temp dirs back. Removing the pool removes the
defect it worked around, so `release_shells` is now a no-op. The regression tests moved with
it: `TestShellLifetime` asserts the `cwd` is deletable straight after `join()`, after
`stop()`, and that a second `Command` in the same `cwd` still works.

---

## 2. A deliberate shutdown is reported as `TIMEOUT` / `FAIL` (FIXED in mo-deploy's vendor copy — awaiting publish, see Coordination below)

`_monitor` breaks out of its loop on `please_stop` **without killing the service**, so after
an intentional stop `self.service.returncode` is `None` by design. `join()` read that `None`
as "it hung":

```python
if self.returncode is None:
    self.kill()
    on_error("{process} TIMEOUT\n{stderr}", ...)   # <- fires on a normal shutdown
if self.returncode != 0:
    on_error("{process} FAIL: ...", ...)           # <- and then again, since kill()
                                                   #    leaves returncode unpolled
```

`Thread.stop()` calls `c.stop()` on every child *before* `join_all_threads`, so at
`stop_main_thread()` every shell still alive takes this path. Measured in mo-deploy: a script
making only read-only `Module.local()` calls, every one succeeding, ended with 8 lines
matching `TIMEOUT` / `At least one thread failed` / `Problem while stopping "MainThread"`.
After the fix: 0.

Removing the pool makes this much rarer here — shells no longer sit around waiting to be
caught by shutdown — but it does not fix it: any `Process` that is `stop()`ed rather than
allowed to finish still reports a phantom `TIMEOUT`. `commands._stop_shell` swallows the
error, so `Command` no longer surfaces it either way; direct `Process` users still do.

### The trap — do not use `please_stop` as the discriminator

The obvious fix is `if not self.please_stop: on_error(...)`. **It is wrong, and it fails
silently in the dangerous direction: it mutes genuine hangs.** `please_stop` is set in *both*
cases by the time `join()` runs — the monitor `Thread` is constructed with
`please_stop=self.please_stop`, i.e. it shares the Process's own signal, and that signal ends
up raised when the monitor ends however it ended. Verified by experiment: with `please_stop`
as the test, a process that genuinely stopped responding reported nothing at all.

Hence `stop_requested`, a plain bool set only by `stop()` — the one thing that unambiguously
means "someone asked for this".

```diff
--- a/mo_threads/processes.py
+++ b/mo_threads/processes.py
@@ -78,6 +78,10 @@ class Process:
         self.name = f"{name} ({self.process_id})"
         self.stopped = Signal(f"stopped signal for {strings.quote(name)}")
         self.please_stop = Signal(f"please stop for {strings.quote(name)}")
+        # SET ONLY BY stop().  please_stop CANNOT ANSWER "DID SOMEONE ASK FOR
+        # THIS?" -- THE monitor THREAD SHARES THAT SIGNAL AND RAISES IT WHEN IT
+        # ENDS, SO IT IS SET AFTER A HANG TOO
+        self.stop_requested = False
         self.second_last_stdin = None
         self.last_stdin = None
         self.stdin = Queue(f"stdin for process {strings.quote(name)}", silent=not self.debug)
@@ -164,6 +168,7 @@ class Process:
         pass
 
     def stop(self):
+        self.stop_requested = True
         self.please_stop.go()
         return self
 
@@ -172,10 +177,18 @@ class Process:
         self.stopped.wait(till=till)  # TRIGGERED BY _monitor THREAD WHEN DONE (self.children is None)
         self.parent_thread.remove_child(self)
         if self.returncode is None:
+            # _monitor LEAVES THE SERVICE RUNNING WHEN IT IS ASKED TO STOP (IT
+            # BREAKS ON please_stop WITHOUT KILLING), SO A MISSING returncode
+            # AFTER A REQUESTED STOP IS THE EXPECTED OUTCOME, NOT A HANG.  ONLY
+            # AN UNASKED-FOR ONE MEANS THE PROCESS STOPPED RESPONDING.
             self.kill()
-            on_error(
-                "{process} TIMEOUT\n{stderr}", process=self.name, stderr=list(self.stderr),
-            )
+            if not self.stop_requested:
+                on_error(
+                    "{process} TIMEOUT\n{stderr}", process=self.name, stderr=list(self.stderr),
+                )
+            # kill() LEAVES returncode UNSET UNTIL POLLED; FALLING THROUGH WOULD
+            # REPORT THE SAME PROCESS A SECOND TIME AS A FAILURE
+            return self
         if self.returncode != 0:
             on_error(
                 "{process} FAIL: returncode={code|quote}\n{stderr}",
```

**REQUIRED — the pair matters more than either test alone:**
- `stop()` then `join(raise_on_error=True)` must **not** raise;
- a process that stops responding **without** anyone calling `stop()` (short `timeout` and
  `startup_timeout`, a long `sleep`) must still raise, with `TIMEOUT` in the message.

Confirmed discriminating in mo-deploy (`tests/test_integration.py::TestProcessShutdown`): the
pre-fix `join` fails the first, and the `please_stop` version fails the second. A single test
would have let the over-broad fix through.

Still open, probably the same teardown path: stray `stdout for {name} queue closed` lines
during test runs (`processes.py:260-280`).

### Coordination

Lives only in `mo-deploy/vendor/mo_threads/processes.py`, committed to mo-deploy's git as
`5e8c987`, **not yet `svn commit`ed** — publishing is Kyle's call. `processes.py` here differs
from mo-deploy's vendored copy *only* by the diff above, so it will land cleanly on the next
`svn-sync` once mo-deploy publishes. Do not apply it by hand in the meantime. Note that
`commands.py` is **no longer** byte-identical to mo-deploy's vendored copy — the pool removal
above landed here first.

---

## 3. Is the shell pool still worth its cost? (ANSWERED — no; removed 2026-07-31, see item 0)

Measured 2026-07-30 on Windows 11, the pool looked like it bought 14 ms/command against the
no-pool floor — real, but only for callers issuing thousands of commands, and not worth an
hour of held `cwd`. Kyle's call: delete it. The follow-up parallel measurement (item 0) shows
that estimate was pessimistic: the saving is 5-7 ms/command once shells are opened
concurrently, because the cost is cmd.exe boot *latency*, which overlaps.

## 4. A killed shell orphans its child process (OPEN — pre-existing, now easier to hit)

`Process.kill()` calls `service.kill()`, which on Windows terminates only `cmd.exe`. Anything
that shell had launched keeps running with the pipes and the working directory it inherited.
`_monitor` kills a shell that has produced no output for `timeout` seconds (`COMMAND_TIMEOUT`
is 5s), so a quiet long-running command leaves an orphan that still holds `cwd` — the same
undeletable-directory symptom as item 1, by a different route.

Killing the tree needs `taskkill /T /F /PID` on Windows (or a process group on POSIX). Not
attempted; no caller has reported it yet.
