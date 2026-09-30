# Unified activity database validation

Implementation date: 2026-09-29. The scope is dictation runtime observations, shared
LLM accounting storage, read-only legacy compatibility, explicit import, GUI timing
summaries and the complete [database contract](../reference/activity-database.md).
User acceptance is pending. No commit, live provider request, microphone capture,
model download, application restart or real-record import was performed.

## Environment and checks

The registered `capswriter` environment uses Python 3.11.15. Conda was unavailable on
the noninteractive PATH, so the registered environment's interpreter was invoked
directly. The initial sandbox could not access the existing pytest temporary/cache
directories; testing continued with approved execution outside that sandbox.

- Default functional suite: 1071 passed, 11 deselected.
- Final focused activity/accounting/diagnostic/history/dashboard checks after contract and timing refinements: 139 passed.
- Bilingual offscreen dashboard layouts: six combinations, English/Chinese at 800,
  1120 and 1440 logical pixels. Labels resolve and minimum widths fit; synthetic
  screenshots were generated. Windows fonts were loaded explicitly for offscreen
  rendering. This does not establish physical monitor/DPI correctness.
- Full source/local-configuration syntax, configured Ruff, configured mypy,
  internal-language, documentation and diff checks passed.
- Coverage collection initially failed while combining branch and statement data
  from parent/child processes, including in an isolated destination. Re-running with
  `--cov-branch` passed the 50% gate. The configured-module report is 85.22%; the
  broader report including subprocess-loaded modules was 57.76%. Qt subprocess
  virtual source files produced non-fatal missing-source warnings. No historical
  coverage files were deleted. The standard-command inconsistency is a separate follow-up.

## Deterministic boundaries

Tests cover foreign keys, future-version rejection, registry presence, an exact
SQL/documentation match, cold versus warm microphone readiness, duplicate readiness,
sub-millisecond LLM phases, direct post-stop duration excluding automatic enter,
result-queue waiting, skipped LLM, failed wake, idempotent terminal outcomes, shutdown
isolation across two owners, independent accounting/statistics switches, shared
request identity, text exclusion, write failure and bounded queue overflow.

Legacy tests cover unchanged source files, repeat import, duplicate suppression,
monthly transaction rollback on identity conflict, rejection of unknown fields or
versions, token type validation, preservation of exact decimal amounts and currencies,
and retention of original elapsed semantics without invented new measurements.
Existing accounting, history, privacy, shortcut, recovery and result-processing
regressions remain part of the default suite.

## Remaining manual scope

- Bluetooth microphone wake variability, first callback timing, short press/cancel,
  wake timeout, device removal and first use after idle suspension on real hardware.
- Physical mixed-DPI/monitor behavior and administrator-window input.
- Real ASR/provider end-to-end correlation and output experience. Pure provider
  inference, ASR queue/inference/alignment measurements and file-transcription
  statistics are not implemented by this increment.
- Abrupt operating-system shutdown and physical disk failure. Uncommitted queued
  observations can be lost; unresolved rows remain explicit instead of being
  silently counted as successful or free.

Existing edits to provider defaults, LLM configuration/service behavior and the local
catalog lock were present before this work. They were preserved. Ignored local
configuration and historical user records were not migrated or overwritten.

## Proposed trial and acceptance gate

This is a project-specific acceptance plan, not evidence of completed manual
validation or a universal database certification period. SQLite engine maturity does
not establish correct application measurements, joins, accounting or migrations.
The trial begins when the user runs the revised application. New writes already
target the unified database in this implementation; this is not a shadow-write mode.

Allow 3 to 7 days of representative use, with at least 100 accepted dictation tasks
across at least three application sessions. Include at least 20 Bluetooth microphone
wakes after idle release, ten starts with an already-ready stream, and the user's
normal LLM-enabled and disabled paths. These are pragmatic coverage targets, not a
statistical reliability guarantee. Low usage extends the trial until the scenarios
are covered. Do not generate paid requests merely to satisfy a sample quota.
Use synthetic tests for provider errors, month rollover, queue overflow and storage
failure; do not damage the real database or force physical disk failure.

Keep a dated, content-free acceptance record with build/revision, schema and metric
versions, session/task counts, tested scenarios, discrepancies, fixes and remaining
limitations. A successful baseline needs zero unexplained reconciliation errors,
duplicate charges, invalid relationships or normal-operation dropped writes. Following
a relevant fix, rerun its boundary tests and observe at least two further normal-use
days for the affected behavior. Elapsed time alone never closes this gate.

| Area | Acceptance evidence |
| --- | --- |
| Storage and identity | Integrity and foreign-key checks pass on a consistent database snapshot. Task, operation and request relationships reconcile; repeated completion does not duplicate a charge or terminal outcome. |
| Task completeness | Independently counted synthetic scenarios match recorded accepted tasks and outcomes. Finished sessions have no unexplained pending rows. Running, cancelled, interrupted and fallback outcomes remain distinct. Forced termination may lose queued observations and must not be described as lossless. |
| Timing meaning | Cold microphone wake measures acquisition through the first audio callback; warm starts are not applicable, not zero. Callback readiness is not a guarantee of acoustic readiness. Post-stop latency ends at output completion and excludes automatic enter. Phase overlap and overhead do not create a promise that child durations sum to a total. |
| LLM and accounting | Preparation, transport request and total action are distinguished. Provider/model groups remain separate. Old/new request IDs deduplicate; exact amounts, currencies, estimates and unknown cost remain distinguishable. Client request duration is not provider inference time or time to first token. |
| Controls and privacy | Diagnostic verbosity does not suppress runtime measurements. Runtime statistics, cost accounting and content saving switches remain independent. The database contains no transcript, prompt, caret content or credentials. Disabled collection is visibly distinguished from an empty period. |
| Persistence and compatibility | Normal restart preserves records. Explicit import passes source-preservation, repeat-import and conflict tests on synthetic or copied fixtures. No real historical migration is required for trial acceptance. Unknown future schemas fail clearly without rewriting the database. |
| User experience | Normal dictation, idle release, cancellation and shutdown remain usable on the user's hardware. Compare collection on/off with the same scripted mock workload and inspect queue/write health; observed regressions require investigation before acceptance. Physical monitor/DPI checks remain separate from offscreen layout tests. |
| Statistics presentation | Displayed counts and summaries reconcile with independent fixture queries for the selected period. Timezone, period boundary, eligibility and sample size are explicit. Query caps are disclosed; truncated data is never presented as a full-period total. |

Before a real migration or older-build rollback, take a consistent SQLite backup and
retain original monthly ledgers. Do not copy a live database blindly. Turning off
runtime statistics does not turn off cost-ledger writes. An older application may not
read new unified records; rollback therefore requires preserving those records and
testing the chosen build against a copy. No reverse migration is currently provided.
Final acceptance must name the tested build and limitations; it is not a guarantee
against every future schema change or hardware failure.

## Statistics page implementation sequence

Build the observation interface before the normal-use trial. Use **Statistics** in
English and the corresponding localized statistics label in Chinese. Preserve the
internal `status` route and stable record identifiers; a display rename needs no
database migration. The following sequence was used for the Statistics implementation
on 2026-09-29. The interface is implemented; the normal-use trial remains pending:

1. Establish one visible time range for runtime summaries, with today, seven-day and
   thirty-day options, local timezone and explicit boundaries. Keep saved-content
   counts distinct from accepted runtime tasks. The current latest-500-task query
   must not be relabeled as a calendar-period summary.
2. Show a compact overview of task outcomes, post-stop latency, cold microphone wake,
   recorded tokens and costs. Separate currencies and unknown or estimated amounts.
   Expose task-count denominators rather than ambiguous success percentages.
3. Use timing tables for microphone wake, dictation start wait, transcription wait,
   result queue, LLM preparation/request/total, output preparation, insertion and
   total post-stop wait. Show median, P95, eligible sample count and unavailable
   samples with definitions. Small cohorts must not imply stable tail estimates.
4. Add task detail with IDs, terminal outcome and recorded operation timings; expand
   LLM subphases, UDP and automatic enter only in detail. Do not fabricate a sequential
   timeline where only duration, rather than actual stage offsets, is available.
   ASR-internal queue/inference and file-transcription metrics remain outside scope.
5. Expose data health separately: collecting/off, active tasks, expected skipped
   measurements, interrupted tasks, missing observations, queue drops, write failures
   and query truncation. Normal in-progress or not-applicable records are not database
   corruption. Keep verbose troubleshooting in Diagnostics.
6. Verify query semantics, bilingual layouts and refresh behavior, then begin the
   trial. Freeze metric meanings during observation; presentation fixes do not need
   to restart the entire trial. A semantic change receives a new metric version and
   targeted revalidation, with incompatible cohorts kept separate.

## Statistics implementation evidence

Implemented six overview cards, shared local-calendar windows, separate runtime and
saved-content counts, timing distributions and definitions, LLM provider/model cost
groups, recent-task inspection and distinct data-health states. Kept the existing
explicit-copy preview and its independent Recent/Today filter. No schema or collection
change is needed for this interface. Runtime reads use one transaction; dates use
half-open boundaries and compatibility accounting reads deduplicate old/new IDs.
The result reserves a one-MiB presentation budget under the owned pipe's two-MiB
limit; exceptionally large metadata reduces detail/group lists with an explicit
partial-results notice, without altering persisted records or aggregated totals.

New deterministic checks cover UTC/local day edges, a year boundary, January in a
thirty-day window across February, more than 500 period tasks, task/measurement caps,
metric-version separation, expected skips versus missing/unresolved observations,
legacy accounting deduplication, currencies/models, stale period responses, task
selection preservation and bounded metadata payloads. Bilingual synthetic screenshots
cover all three tabs at 800, 1120 and 1440 logical pixels. Width-dependent table row
heights were corrected after visual inspection. Physical DPI and Bluetooth trial
evidence remain pending. No real microphone/provider access or historical import ran.

Validation on Python 3.11.15: 1081 default tests passed, 11 deselected; after the final
payload-budget and compatibility-projection refinements, all 35 focused
activity/calendar/accounting/dashboard checks passed. Syntax, configured Ruff,
configured mypy, internal-language, documentation and diff checks passed. Screenshot
checks covered eighteen tab/language/width combinations; synthetic images were
visually inspected. The initial sandbox denied access to pytest's existing temporary
directory, so tests used approved execution outside the sandbox. The prior coverage
measurement above was not repeated for this presentation/query increment.

## First real-use audit, 2026-09-30

Status: acceptance remains open. Read-only examination found a healthy SQLite file
and correctly reconciled real task/request observations, but also synthetic-record
contamination and an optional-output measurement problem. No real database records
were edited, deleted or imported during this audit. Diagnostic comparison used only
structural event/request/task metadata, never message or content fields.

The earlier assurance of isolated synthetic validation was incomplete:
`test_websocket_shutdown.py` constructed a real `ActivityRecorder` through the client
constructor while mocking its other collaborators. Its `isolated_client` fixture
neither redirected the recorder nor closed it. Repeated validation wrote ten synthetic
run rows and one shared `startup-test` task to the ordinary database. Five attempts
each produced start-wait and microphone-wake failure measurements. All ten operations
reference a task owned by another run. Single-column foreign keys accept this semantic
ownership mismatch. The real-use run does not exhibit that mismatch.

The audit corrected the fixture to allocate a per-client temporary recorder with
explicit synthetic settings and drain it during cleanup. A new regression verifies
the temporary destination, terminal record, closed run and stopped writer thread.
All 25 tests in that module passed. This prevents further writes from this fixture;
it does not remove the pre-existing synthetic rows. Historical cleanup and application
ownership safeguards remain follow-up work, not an implicit acceptance or migration.

The real-use sample contains 45 terminal dictations: 42 completed and three retaining
original text after LLM connection failures. Forty cold-wake observations and five
already-ready cases are distinguishable. All required top-level timing measurements
are present. Forty-four LLM requests reconcile with terminal diagnostic events; one
dictation skipped LLM. Native request/accounting duration, normalized usage, normalized
latest cost revisions and independent decimal rate calculations agree. Independent
calendar-window counts, median/P95 and combined legacy/native accounting totals agree
with the presentation queries. There are no observed real-task cross-run links,
pending tasks, negative durations, missing required metrics, recorded queue drops or
recorded write errors. This is observed consistency, not proof of every hardware or
crash path. Normal post-stop totals include small unassigned client overhead beyond
the separately measured stages; that difference is not counted as lost data.
Saved-text summary counts also match independent archive-header counts for today,
seven days and thirty days, without exposing transcript content in audit output.
After the isolated test rerun, the ordinary database still contained the same task,
run and measurement counts as before it; no new synthetic records were added.

UDP output was disabled in the inspected configuration, yet all 45 output paths
recorded successful `dictation.udp` durations. The instrumentation wraps the broadcaster
even when it immediately returns without sending. These observations describe wrapper
invocation, not actual UDP output. Disabled/no-target cases should be explicitly not
applicable, and send failures should not appear successful. Existing observations
must not be retroactively inferred from today's configuration alone. Automatic-enter
and server-internal ASR observations were absent; absence does not establish zero cost
or successful validation of those paths.

The 3-to-7-day/100-task gate remains pending. Before acceptance, isolate or explicitly
repair the known synthetic records with a consistent backup, strengthen run/task
semantic validation, and correct optional UDP measurement availability/outcomes.

## Expanded timing summaries, 2026-09-30

Added arithmetic mean, maximum and the latest recorded observation to each timing
group, retaining median, nearest-rank P95 and eligibility counts. Aggregates use one
shared successful-stage cohort and remain separated by metric version/provider/model.
The latest observation is independent of that cohort: a failure, missing value or
not-applicable stage remains visible with its outcome and task start time. It is not
replaced with an earlier successful value. Overview duration cards emphasize this
latest observation and show all four aggregates and the eligible sample count below.
P95 small-sample markers also appear on the cards. Other-sample counts share one table
column; narrow windows retain horizontal scrolling and readable metric names.

No persisted field or metric boundary changed, so existing records require no migration.
The first-day audit findings above remain separate open work. Synthetic regression
checks cover an extreme outlier, arithmetic mean/max, reverse identifier ordering,
failed/skipped/lost latest observations, empty cohorts, valid zero duration, card values
and bilingual rendering across three widths and all three tabs. All 52 focused
statistics/activity/dashboard/refresh checks passed (139 unrelated tests deselected).
Syntax, configured Ruff/mypy, internal-language, documentation and diff checks passed
on Python 3.11.15. Representative synthetic screenshots were visually inspected.
The full default suite and coverage were not repeated for this query/presentation-only
increment. No real database writes, hardware input or provider calls were performed.

## Timing card redesign and calendar periods, 2026-09-30

The user rejected the dense aggregate hint layout. Replaced it with a dedicated
response-time section: two responsive cards, each containing a highlighted latest
observation, textual outcome badge, separate task timestamp, four labeled numeric
tiles and sample guidance. The four usage summary cards remain compact. Timing
cards stack below 700 logical pixels; nested minimum-size constraints preserve
numeric labels. Screenshot fixtures now use a scroll container like the real page,
instead of forcing all content into a native top-level window.

Added this-week (Monday), this-month and this-year windows through today, retaining
today and the rolling seven/thirty-day choices. Calendar scope remains shared by
runtime, saved-text and accounting summaries. Accounting reads now have a shared
30,000-record budget in addition to the existing per-month cap; reaching a cap
keeps the partial-results disclosure. No database schema or persisted data changed.

Regression coverage includes week/year rollover, Sunday/Monday boundaries, leap
February, half-open runtime/accounting boundaries and all twelve accounting months
with budget truncation. Bilingual snapshots cover 600, 800, 1120 and 1440 logical
pixel content widths and all three tabs. Geometry assertions check separate tiles,
numeric label height and spacing between stacked cards. Representative English and
Chinese snapshots were visually inspected. Real multi-monitor/mixed-DPI interaction
and user visual acceptance remain pending; this increment does not complete the
database trial or resolve the separate first-day audit findings above.

Final verification: 65 focused tests passed, 139 unrelated tests deselected. Syntax,
configured Ruff/mypy, internal-language, documentation and diff checks passed on
Python 3.11.15. Full-suite/coverage checks were not repeated for this UI/query-only
increment. Tests used synthetic local data; no hardware, provider calls or production
database writes were performed.

## Active-page sizing and consistent field help, 2026-09-30

Following user review, removed the extra timing-section heading and routine completed
badges from the overview cards. Exceptional outcomes remain explicit; completed
outcomes remain available in task details and observation tooltips. Replaced inline
definition paragraphs with localized hover help on overview titles, metric labels,
column headings and stage names, including task-detail stages. Card/table aggregate
definitions share the same help resources. Small-sample P95 values retain a marker.

Tables fit their row counts up to the existing bounded scroll height. The tab container
now fits the active page instead of reserving space for the largest hidden page, and
recomputes after tab selection, row updates and width changes. Empty notice labels no
longer reserve space; later copy/error notices become visible when needed.

Validation: 67 focused statistics/activity/dashboard tests passed (139 unrelated tests
deselected), including tab switching, row growth/shrinkage, routine-success versus
exceptional badges, localized header help and active-page geometry. Bilingual
screenshots at four content widths cover all three tabs; representative screenshots
were visually inspected. Syntax, configured Ruff/mypy, internal-language, documentation
and diff checks passed on Python 3.11.15. Full-suite/coverage checks were not repeated
for this presentation-only follow-up. No production database writes or hardware/provider
calls were made. Physical mixed-DPI interaction and final user acceptance remain pending.

## Simplified statistics overview, 2026-09-30

Removed the three detail table tabs and Data health block at the user's request,
including their unused widget implementations and table-specific tests. The four
overview names now use the same card-title role as the duration headings, retaining
localized hover definitions. Calendar filters, timing cards, outcome counts, recent
text previews and explicit read-error/partial-result notices remain. Collection,
query contracts, database records and CLI task inspection were not changed.

Clarified that not-applicable measurements represent stages not required by that
task, such as an already-ready microphone or no LLM action. They count stage records,
not tasks, and are excluded from duration distributions rather than treated as zero.

Validation: 65 focused tests passed, 139 unrelated tests deselected. Bilingual layouts
at four widths verify equal title styling, hover definitions, intact timing values and
absence of table tabs/Data health. Representative screenshots were visually inspected.
Syntax, configured Ruff/mypy, language, documentation and diff checks passed on Python
3.11.15. Full-suite/coverage checks were not repeated for this presentation-only change.
No production data was changed or hardware/provider calls made. Final visual acceptance
and physical mixed-DPI interaction remain pending.

## Source acceptance, 2026-09-30

After the final settings layout refinement, the user accepted the work and explicitly
authorized commit. This accepts the source implementation and final Statistics UI;
it does not complete the real-use database trial or close the first-day audit findings.
Historical test-data remediation, run/task semantic ownership hardening and optional
UDP measurement corrections remain explicitly tracked in TODO. The committed tests
use isolated activity destinations; user records are not migrated or rewritten.

Commit-time validation of the final combined source: 1106 default tests passed,
11 deselected, on Python 3.11.15. The final settings module passed all 160 tests;
bilingual all-page layouts passed at normal, 150% and 200% simulated scaling.
Syntax, configured Ruff/mypy, internal-language, documentation and staged-diff
checks passed. This source verification does not replace the open real-use trial.
