# Server result timeout after system resume

Investigation and source fix on 2026-09-23. Automated checks do not establish
real sleep/wake or GPU recovery. The user accepted the source changes for commit
on 2026-09-23; real sleep/wake validation remains pending.

## Evidence and diagnosis

Local per-process diagnostic archives and Windows power events establish this
sequence (local time, UTC+08:00):

- 12:44:50: the last recognition task completed and its result was delivered.
- 13:15:56: Windows recorded entry into sleep (Kernel-Power event 42).
- 14:49:23.500: Windows synchronized the clock on resume. The power troubleshooter
  recorded return from low power at 14:49:25.
- 14:49:23.561: the server reported `ResultQueueReadTimeout` as a critical shared
  channel failure. The listener closed and application cleanup began.
- 14:49:26: the recognition subprocess ended its task loop and performed cleanup;
  the client could no longer reconnect to the listener.
- 14:49:29: the parent reported incomplete worker termination, removed its tray
  icon and finished application cleanup.

The immediate exit trigger is established by the parent log. The likely cause
is the result watchdog treating a long scheduling pause as a damaged IPC read.
It used an absolute monotonic deadline and could expire before the reader thread
or its asynchronous completion resumed. Its completed-task snapshot could also
be stale when the caller resumed. Synthetic scheduling gaps reproduce this
failure, including when a normal empty-queue response is about to arrive.

Routine connection idle timeouts before sleep closed individual connections;
the client repeatedly reconnected. They do not explain the whole service exit.
The worker's later cleanup does not prove IPC or GPU health, but there is no
earlier worker-exit diagnosis in the incident's server logs.

Windows Error Reporting also submitted kernel/graphics reports around resume.
Submission timestamps alone do not date the underlying faults; these records
do not establish a GPU crash as the cause of this server exit. Incomplete worker
termination remains a separate investigation in [TODO](../../TODO.md).

## Implemented behavior

- Keep exactly one pending result read. Check its current completion state before
  declaring a timeout, preserving completed results and empty-queue responses.
- When a long scheduling gap is observed, allow that same read one recovery
  window of at most five seconds (capped by the configured read timeout).
  Never retry the IPC read or accumulate blocked executor threads.
- Give an expired worker heartbeat the same bounded opportunity after a monitor
  scheduling gap. Do not overwrite the shared heartbeat. Further grace requires
  actual worker progress, so repeated gaps cannot hide a continuously stuck worker.
- Continue immediate worker-death/failure detection and normal cancellation and
  shutdown. Broken queues and persistent stalls still stop the service.
- Log scheduling gaps and grace durations through localized notices, with no
  user content. Configuration values and wire formats are unchanged.

This addresses idle runtime supervision. It does not replay interrupted tasks,
restart damaged GPU contexts or change deadlines for active requests, model
startup, alignment or network sends. A scheduling gap is evidence of a pause,
not a claim that Windows definitely slept.

## Verification

Environment: the existing `capswriter` environment, Python 3.11.15.
Before the fix, six of ten new result-reader cases failed, including healthy
result/empty-queue recovery. The new suite adds seven worker-monitor cases.
All use synthetic clocks, queues and process states; no actual sleep, microphone,
GPU command, model loading or cloud request is required.

The default suite passed: **604 passed, 8 deselected**, including all 17 new
resume cases. Compileall (including existing local configurations), Ruff,
internal-language, documentation and diff checks passed. The first full-suite
attempt hit access denial in pytest's shared system temporary directory; rerunning
with a unique workspace `.cache/` base directory resolved the fixture errors.
No application startup or real sleep/wake test was performed.

Manual validation remains: restart the source server, verify ordinary dictation,
leave it idle, sleep for longer than the read timeout, then wake and confirm the
server remains available and fresh dictation succeeds. Repeat a sleep/wake cycle
and normal tray exit. Existing connections may reconnect after idle timeout.
Check for orphan model processes after exit. Do not treat successful idle resume
as validation of sleeping during active inference or model startup.
