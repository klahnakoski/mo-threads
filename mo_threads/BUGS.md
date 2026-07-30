# mo_threads — known defects

Found while modernising `mo-deploy` on 2026-07-30. All three concern the `Command`
shell pool in `commands.py`. Item 1 is already written and verified in mo-deploy's
vendored copy and only needs to land here; items 2 and 3 are untouched.

---

## 1. A pooled shell holds its `cwd`, so the directory cannot be deleted (FIXED in mo-deploy's vendor copy — needs to land here, with tests)

`Command` pools one shell per `(cwd, env, debug, shell)`. When a command finishes,
`Command._worker` calls `return_process` (`commands.py:142`), which stamps
`AVAIL_TIMEOUT` (one hour) on the process and parks it in `avail_processes`. That shell
keeps `cwd` as its working directory the whole time, and **Windows will not let anyone
remove a directory a live process is sitting in**.

Measured: a `mo_files.TempDirectory` used as a `Command` cwd still existed 12s after its
`with` block exited, while an otherwise identical unused one was gone.

This is not cosmetic for callers that run commands in temp dirs. `mo-deploy`'s
`Module.run_tests` allocates two per python version per module — a virtualenv and a git
worktree — and uses both as `cwd`. Every one of them survived the deploy.

It got *louder* once `mo_files.delete_daemon`'s inverted guard was fixed (that daemon
previously returned without ever calling `file.delete()`, so the leak was silent). It now
genuinely retries every 10s and, from the second attempt on, logs
`problem deleting file {file}` — so each held directory becomes a warning every ten
seconds for the rest of the process's life.

### The fix

Add `release_shells(cwd)`, backed by `LifetimeManager.stop_processes_in()`. Only
`avail_processes` are evicted — an `inuse` shell is still running someone's command and
returns to the pool normally when it finishes. The exit-then-join teardown is factored
out of `_stop_stale_processes` into `_exit_processes` so both paths shut shells down the
same way rather than duplicating that discipline.

This patch is against `dev` as of `mo_threads/commands.py` matching mo-deploy's vendored
copy byte-for-byte, so it applies cleanly:

```diff
--- a/mo_threads/commands.py
+++ b/mo_threads/commands.py
@@ -157,6 +157,20 @@ def _stderr_relay(source, destination, please_stop=None):
     destination.add(PLEASE_STOP)
 
 
+def release_shells(cwd):
+    """
+    STOP ANY IDLE POOLED SHELL SITTING IN cwd, SO cwd CAN BE DELETED
+
+    RETURNS THE NUMBER STOPPED.  SAFE TO CALL WHEN NONE EXIST, AND SAFE TO USE
+    cwd AGAIN AFTERWARD -- A NEW SHELL IS SIMPLY OPENED.
+    """
+    with lifetime_manager_locker:
+        manager = lifetime_manager
+    if not manager:
+        return 0
+    return manager.stop_processes_in(cwd)
+
+
 class LifetimeManager:
     def __init__(self):
         global lifetime_manager
@@ -251,6 +265,40 @@ class LifetimeManager:
             else:
                 logger.error("process not found")
 
+    def stop_processes_in(self, cwd):
+        """
+        SHUT DOWN IDLE SHELLS SITTING IN cwd, SO THE DIRECTORY CAN BE DELETED
+
+        A POOLED SHELL KEEPS cwd AS ITS WORKING DIRECTORY FOR AVAIL_TIMEOUT,
+        AND WINDOWS WILL NOT LET ANYONE REMOVE A DIRECTORY A PROCESS IS SITTING
+        IN.  CALL THIS WHEN DONE WITH A TEMPORARY DIRECTORY.
+
+        ONLY IDLE SHELLS ARE TAKEN; AN inuse SHELL IS STILL RUNNING SOMEONE'S
+        COMMAND, AND WILL BE RETURNED TO THE POOL WHEN IT FINISHES.
+        """
+        cwd = os_path(cwd)
+        with self.locker:
+            doomed = [p for p in self.avail_processes if p[0][0] == cwd]
+            if doomed:
+                self.avail_processes[:] = [p for p in self.avail_processes if p[0][0] != cwd]
+        DEBUG and logger.info("stop {num} processes in {cwd}", num=len(doomed), cwd=cwd)
+        self._exit_processes(doomed)
+        return len(doomed)
+
+    def _exit_processes(self, processes):
+        for _, process, _ in processes:
+            try:
+                if not process.stopped:
+                    process.stdin.add("exit")
+            except Exception:
+                pass
+
+        for _, process, _ in processes:
+            try:
+                process.join(raise_on_error=True)
+            except Exception:
+                pass
+
     def _stop_stale_processes(self, too_old):
         DEBUG and logger.info("stop stale processes")
         with self.locker:
@@ -263,18 +311,7 @@ class LifetimeManager:
                     fresh.append((key, process, last_used))
             self.avail_processes[:] = fresh
 
-        for _, process, _ in stale:
-            try:
-                if not process.stopped:
-                    process.stdin.add("exit")
-            except Exception:
-                pass
-
-        for _, process, _ in stale:
-            try:
-                process.join(raise_on_error=True)
-            except Exception:
-                pass
+        self._exit_processes(stale)
 
         if DEBUG and stale:
             for key, process, last_used in stale:
```

**REQUIRED — `mo_threads` has no test for any of this:**
- a `TempDirectory` used as a `Command` cwd is undeletable *before* `release_shells` and
  deletable *after* (this is the assertion that fails if the fix is reverted — the other
  two below pass either way);
- the same `cwd` still works afterward: a second `Command` opens a fresh shell and returns
  `returncode == 0`;
- `release_shells` on a directory with nothing pooled returns `0` and does not raise;
- an `inuse` shell is **not** killed — start a long-running command, call `release_shells`
  on its `cwd` from another thread, and assert the command still completes. This is the
  case with no coverage anywhere and the one most likely to be broken by a refactor.

Equivalent tests exist in mo-deploy at `tests/test_integration.py::TestShellRelease`, but
they belong here — mo-deploy only tests the three it depends on.

### Coordination — read before editing `commands.py`

The fix currently lives **only** in `mo-deploy/vendor/mo_threads/commands.py`, committed to
mo-deploy's git (`257cc71`) but **not yet `svn commit`ed**. So right now:

- this repo's `dev` still has the unpatched file;
- mo-deploy's vendored copy has the patch.

Whoever moves next should pick one direction — either apply the patch here and let it reach
mo-deploy on its next vendor refresh, or `svn commit` from mo-deploy and pull it in here —
**not both**, or the two edits collide on the sync.

---

## 2. A deliberate shutdown is reported as `TIMEOUT` / `FAIL` (NOT FIXED)

`processes.py:170-186`:

```python
def join(self, till=None, raise_on_error=True):
    on_error = logger.error if raise_on_error else logger.warning
    self.stopped.wait(till=till)
    self.parent_thread.remove_child(self)
    if self.returncode is None:
        self.kill()
        on_error("{process} TIMEOUT\n{stderr}", ...)
    if self.returncode != 0:
        on_error("{process} FAIL: returncode={code|quote}\n{stderr}", ...)
```

The `TIMEOUT` branch does not return, so one process can report *both* `TIMEOUT` and
`FAIL`. More importantly, at `stop_main_thread()` a pooled idle shell is **killed** rather
than allowed to exit, so `returncode is None` — and a teardown that went exactly as
intended is indistinguishable from a command that genuinely hung.

Measured in mo-deploy: a script making only read-only `Module.local()` calls, every one
succeeding, still ends with 8 lines matching `TIMEOUT` / `At least one thread failed` /
`Problem while stopping "MainThread"`.

Needs a flag (or a distinct path) so an intentional stop is quiet. Note item 1 does **not**
help here — `release_shells` only covers directories a caller explicitly releases, and the
noisy shells are the long-lived ones (in mo-deploy, each managed repo's own directory),
which stay pooled until shutdown kills them.

Related, probably the same teardown path: stray `stdout for {name} queue closed` lines
appear during test runs (`processes.py:260-280`).

---

## 3. Is the shell pool still worth its cost? (QUESTION — answer this before doing 1 or 2)

The pool exists because opening a shell was expensive on older Windows. That premise is
years old and worth re-measuring on Windows 11 before either of the above is built out.

If spawning a shell is now cheap, the better move is to delete the pool rather than keep
tuning it — which would dissolve items 1 and 2 outright, along with `AVAIL_TIMEOUT`,
`INUSE_TIMEOUT`, `STALE_MAX_AGE` and the whole `LifetimeManager` lifetime dance.

If it is still expensive, `AVAIL_TIMEOUT = 60 * 60` (`commands.py:31`) deserves a second
look regardless — an hour is a long time to hold a working directory hostage on the chance
someone runs another command in it.
