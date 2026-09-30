# Activity database contract

This document specifies the implemented version-one database, all persisted fields,
measurement semantics, compatibility rules and operational limits. It covers microphone
dictation. File transcription and server inference instrumentation are not collected by
this implementation. The executable schema and metric registry are in
[schema.py](../../core/activity/schema.py); the user guide is
[runtime statistics](../user/runtime-statistics.md).

## Storage and ownership

The client writes `activity.sqlite3` inside the effective `tracking.directory` from
`LLM/costs.toml` or its public template, normally `llm-costs/activity.sqlite3`.
This preserves existing custom accounting directories. A running client snapshots the
database destination at startup; changing that directory requires restarting the client.
Attached GUI queries use the running client's destination. Standalone queries use saved
configuration. No existing local configuration is rewritten by this feature.

The database holds content-free runtime observations and LLM accounting. Detailed JSONL
diagnostics, Markdown transcripts and audio remain separate. Log severity and diagnostic
file persistence do not filter database observations. `save_runtime_statistics` defaults
to true, requires restart, and independently gates task/timing collection. The existing
client and TOML accounting switches still gate monetary records and estimates. Provider
usage observed during enabled runtime collection can be saved even with accounting off.
Runtime collection does not construct heuristic costs when accounting is disabled.

There is no automatic database expiry or deletion. Diagnostic retention does not apply.
No new cleanup UI is introduced. Future retention must delete measurements independently
of bills, retain referenced identity rows, and never cascade runtime cleanup into costs.

Runtime producers submit metadata to one bounded client queue (512 entries, 128 active
tasks). The writer owns its SQLite connection per transaction. Accounting uses short
transactions on background `asyncio.to_thread` calls. SQLite serializes concurrent writers;
there is no shared connection across threads, UI objects, audio callbacks or processes.
Foreign keys are enabled on every connection; lock timeout is two seconds. The default
rollback journal is used. A successful commit is stronger than queued diagnostic delivery,
but power-loss or process-kill durability is not promised for observations still in memory.

Audio callbacks only capture the first callback's `perf_counter_ns()` timestamp before
publishing readiness. They never enqueue database writes, log or wait for storage.
Normal shutdown terminates owned unfinished tasks and drains the writer with bounded
waiting. A killed process can leave `running` tasks and a run with no end time. Readers
show these as unfinished or interrupted; they do not guess that another active process
is dead. There is no startup operation that marks every unclosed run as crashed.

Write failures and queue overflow produce content-free warnings and in-memory counters.
The counters are persisted on the next successful write. A database that remains
unwritable cannot contain its own failure report. Failure of observation does not replace
usable recognition or LLM output. Queue loss means incomplete statistics, not zero work.

## Identity and version rules

| Identity | Meaning |
| --- | --- |
| `run_id` | Random UUID hex for one client observation owner; independent of JSONL sink run IDs |
| `task_id` | Existing recorder UUID, allocated before microphone resume and shared by ASR, LLM and output |
| `operation_id` | Stable UUID derived from run/task/metric for one task observation; LLM roots derive from request ID |
| `request_id` / `llm_requests.id` | Existing LLM action/request identifier, including pre-dispatch failures; no retry is introduced |
| `measurement_id` | Currently equal to its operation ID; unique per operation/metric/version |
| ASR identity | Existing service ownership remains `(socket_id, task_id)`; database linkage never replaces it |

Version one creates one LLM root operation for each eligible action and one attempt.
LLM preparation/request/application-stage operations refer to that root. Transport trace
operations remain diagnostic details. No claim of server-internal inference or DNS-only
timing is made. `asr_bindings` reserves an explicit cross-process binding contract, but is
not populated until a validated, versioned server observation exchange is implemented.

`PRAGMA user_version=1` is the database structure version. `metric_definitions.version=1`
is a metric's semantic version. `measurements.method_version=1` identifies the implemented
measurement method. Configuration version 2.7 is unrelated. `runs.app_version` currently
contains `source`, not the configuration version or a fabricated release number.

New optional fields can use compatible defaults. A changed start/end boundary requires
a new metric semantic version; do not update old definitions in place. Measurement
precision changes preserve semantic identity but require an appropriate method version.
Future structural migrations must provide an explicit backed-up, transactional migration
and compatibility tests. Version one initializes new databases only; a future database
version is rejected for both reading and writing. No automatic legacy import runs at startup.

## Field dictionary

SQLite `TEXT` values are UTF-8. `INTEGER` booleans use 0/1. UTC timestamps generated by the
runtime use ISO 8601 with an offset and microseconds. Legacy accounting timestamps keep
their original offset. Offsets and durations are integer microseconds from a local
monotonic clock; they must not be subtracted across runs or machines. Integer storage
does not imply microsecond measurement accuracy; `resolution_ns` records clock resolution.

All foreign keys below use SQLite's default NO ACTION, without cascading deletion.
`NOT NULL` is stated explicitly in the SQL definition at the end. Primary identifiers
are always supplied by application writers. No field contains transcript, prompt,
caret context, audio, API keys, authorization headers or a full provider endpoint.

### schema_migrations

| Field | Type | Meaning |
| --- | --- | --- |
| `version` | INTEGER, PK | Applied database version |
| `applied_at` | TEXT | UTC initialization/migration time |
| `checksum` | TEXT | SHA-256 of the DDL executed for this version |

### runs

| Field | Type | Meaning |
| --- | --- | --- |
| `run_id` | TEXT, PK | Observation owner identity |
| `component` | TEXT | Currently `client` |
| `app_version` | TEXT | Producer version label; currently `source` |
| `started_at` | TEXT | Owner creation time |
| `ended_at` | TEXT, nullable | Successfully recorded normal close; null is not proof of a crash |
| `clock` | TEXT | `time.get_clock_info('perf_counter').implementation` |
| `resolution_ns` | INTEGER | Positive nominal clock resolution in nanoseconds |
| `dropped` | INTEGER, default 0 | Rejected queue observations known to this owner |
| `write_errors` | INTEGER, default 0 | Observation/write failures known to this owner |

### tasks

| Field | Type | Meaning |
| --- | --- | --- |
| `task_id` | TEXT, PK | One accepted dictation identity |
| `run_id` | TEXT, FK runs | Client owner |
| `started_at` | TEXT | Task observation creation time; precise accepted-start timing uses the monotonic boundary |
| `stopped_at` | TEXT, nullable | Stop accepted; null for cancellation before stop |
| `ended_at` | TEXT, nullable | Terminal task observation time |
| `state` | TEXT | `running`, `finished`, `interrupted` |
| `outcome` | TEXT, nullable | `completed`, `fallback`, `failed`, `cancelled`, `not_inserted`, `interrupted`; null while unfinished |
| `error_code` | TEXT, nullable | Controlled failure code or exception class, never exception text |
| `complete` | INTEGER boolean, default 1 | No owner-level observation loss known at finalization; not a guarantee against future process loss |

`fallback` means original text was output after LLM failure. Individual LLM operations
still say `failed`; a fallback is not counted as a successful LLM request. `completed`
means the local text output operation returned, not verification of text in another app.
UDP/automatic-enter failures can make the task fail even after text insertion succeeded;
the successful insertion remains independently observable.

### operations

| Field | Type | Meaning |
| --- | --- | --- |
| `operation_id` | TEXT, PK | Idempotent operation observation identity |
| `task_id` | TEXT, nullable FK tasks | Associated dictation, absent for standalone LLM actions |
| `run_id` | TEXT, FK runs | Clock and observation owner |
| `parent_id` | TEXT, nullable FK operations | LLM action root for LLM stage observations |
| `kind` | TEXT | Stable metric/operation name |
| `started_offset_us` | INTEGER, nullable | Start relative to owner monotonic epoch; null for summed phase durations |
| `ended_offset_us` | INTEGER, nullable | End relative to the same epoch; never before recorded start |
| `outcome` | TEXT | Operation-specific result; includes `completed`, `failed`, `cancelled`, `not_sent`, `skipped`, `interrupted`, `fallback`, `not_inserted` |
| `error_code` | TEXT, nullable | Reserved operation failure classification; version-one timing writer leaves null |

Operations are written when their measurement is available, not as durable start events.
A crash can leave the task or accounting request started without all operation rows.
No exact-once delivery claim follows from idempotent operation IDs.

### metric_definitions

| Field | Type | Meaning |
| --- | --- | --- |
| `name` | TEXT, composite PK | Stable machine metric name |
| `version` | INTEGER, composite PK | Immutable semantic revision |
| `unit` | TEXT | `us` |
| `start_boundary` | TEXT | Stable start boundary identifier |
| `end_boundary` | TEXT | Stable end boundary identifier |
| `aggregation` | TEXT | `distribution_by_outcome_no_parent_sum`: compare outcomes separately; do not sum overlapping parent/child stages |

### measurements

| Field | Type | Meaning |
| --- | --- | --- |
| `measurement_id` | TEXT, PK | Idempotent observation identity |
| `operation_id` | TEXT, FK operations | Owning operation |
| `metric` | TEXT, composite FK metric_definitions | Metric name |
| `metric_version` | INTEGER, composite FK metric_definitions | Definition version |
| `value_us` | INTEGER, nullable | Nonnegative measured microseconds; null unless observed |
| `availability` | TEXT | `observed`, `not_applicable`, `not_collected`, `lost` |
| `source` | TEXT | `client`, `server`, `legacy`; current instrumentation emits `client` |
| `method_version` | INTEGER | Measurement method revision |
| `reason` | TEXT, nullable | E.g. `already_ready`, `no_llm_action`, `transport_not_called`, `output_not_requested`, `invalid_clock_boundary` |

Observed values require a non-null nonnegative duration; all other availability states
require null. Unique `(operation_id, metric, metric_version)` prevents double counting.
An already-open microphone has a not-applicable wake value, not a zero-duration cold wake.
Cancelled/failed operations may have measured elapsed time but do not enter success latency
distributions. Missing rows after loss are different from explicit not-applicable rows.

### asr_bindings

| Field | Type | Meaning |
| --- | --- | --- |
| `binding_id` | TEXT, PK | Binding identity |
| `task_id` | TEXT, FK tasks | Local dictation |
| `connection_id` | TEXT | Connection attempt identity; new on reconnect |
| `server_run_id` | TEXT | Server process identity |
| `socket_id` | TEXT | Server connection identity |
| `wire_task_id` | TEXT | Task ID supplied on that connection |

Unique `(server_run_id, socket_id, wire_task_id)` preserves connection-scoped ownership.
This table is reserved and empty in version one. ASR queue/inference/alignment measurements
are unavailable, not inferred from client waiting time. The wire protocol is unchanged.

### llm_requests

| Field | Type | Meaning |
| --- | --- | --- |
| `id` | TEXT, PK | Existing request identifier |
| `operation_id` | TEXT, nullable FK operations | Runtime LLM root, when observed; null for cost-only, imported or incomplete observations |
| `started` | TEXT | Original request start timestamp with offset |
| `month` | TEXT | Original start timestamp's `YYYY-MM` accounting bucket |
| `status` | TEXT | `unfinished`, `completed`, `failed`, `cancelled`, `not_sent`, `skipped` |
| `accounting_enabled` | INTEGER boolean | Whether the row belongs to the monetary accounting view |
| `record` | TEXT, JSON | Version-one compatibility projection described below; maximum writer/read limit 65,536 characters |
| `origin` | TEXT, default native | `native` or `legacy_v1` |
| `legacy_hash` | TEXT, nullable | SHA-256 of imported source JSON for repeat/conflict detection |

The compatibility JSON avoids breaking existing accounting consumers while typed columns
and child tables support identity and numeric queries. It is not an arbitrary payload bag.
No full HTTP response or provider configuration may be placed in it.

Accounting records have these fields (nullable unless required by the producer):

| JSON field | Meaning |
| --- | --- |
| `schema_version` | 1; independent of database structure version |
| `id`, `started_at`, `finished_at`, `status` | Identity, original offset timestamps and accounting lifecycle |
| `error_category` | Controlled LLM failure category |
| `provider`, `model`, `preset` | Configuration identifiers captured for the request |
| `endpoint_hash` | Existing truncated endpoint digest; no endpoint URL |
| `input_token_estimate` | Heuristic input count for cost estimation |
| `token_estimator` | Existing estimator ID: `utf8_bytes_div_3_plus_message_overhead` |
| `max_output_tokens` | Requested limit used for incomplete-request cost scenarios |
| `rate` | Price snapshot or null; allowed keys below |
| `elapsed_ms` | New rows use the same LLM action total as diagnostics; imported rows preserve legacy elapsed semantics |
| `accounting` | Existing usage/money/provenance projection below |

The `accounting` object contains `usage` (provider counters), `estimated_usage` when
needed, `usage_source` (`provider`, `mixed_estimate`, `heuristic`, `unknown`),
`invalid_usage` (boolean), `amount` (decimal string/null), `currency` (currency/null),
`cost_source` (`provider_reported`, `rate_estimate`, `token_estimate`, `possible_cost`,
`not_sent`, `unknown`), `reported_cost_field`, `http_status`, and `dispatch`
(`unknown`, `attempted`, `not_sent`). Counts and estimated counts must not be added together.

Allowed rate keys are `model`, `currency`, `input`, `output`, `cached_input`, `cache_write`,
`reasoning`, `source`, `updated`, `assumption`, `valid_until`, `reported_cost_field`,
`expired`. Numeric rates are decimal strings per million tokens. The configured endpoint
is removed before persistence. A missing/expired/inapplicable rate is not free service.

Statistics-only records contain `schema_version`, `id`, `started_at`, `finished_at`,
`status`, `elapsed_ms`, `provider`, `model`, `preset`, `config_revision`, `http_status`,
`dispatch`, `error_category`, `usage`, `usage_invalid`. `config_revision` is the existing
process-scoped keyed diagnostic digest, not a reusable credential hash. These records
contain no rate or heuristic charge. When both paths write the same request, accounting
owns the compatibility JSON and runtime attaches the operation link without overwriting
accounting. The operation relation preserves runtime stage detail in either case.

### llm_usage

| Field | Type | Meaning |
| --- | --- | --- |
| `request_id` | TEXT, composite PK/FK llm_requests | Request |
| `category` | TEXT, composite PK | `input_tokens`, `output_tokens`, `total_tokens`, `cached_tokens`, `cache_write_tokens`, `reasoning_tokens` |
| `source` | TEXT, composite PK | `provider` or `estimate` |
| `quantity` | INTEGER | Nonnegative validated count, writer limit below 10^12 |
| `valid` | INTEGER boolean | Whether the provider usage group passed consistency validation; estimates retain the original uncertainty flag |
| `method` | TEXT, nullable | Token estimator ID for estimated values |

Cached/reasoning counts are subsets, not additional totals. Missing categories have no
row and are not automatically zero. Runtime-only collection writes provider observations;
the accounting path also maintains estimates. The unique key is request/category/source.

### llm_costs

| Field | Type | Meaning |
| --- | --- | --- |
| `request_id` | TEXT, composite PK/FK llm_requests | Request |
| `revision` | INTEGER, composite PK | 0 for initial unfinished estimate, 1 for terminal accounting |
| `amount` | TEXT, nullable | Exact decimal amount; unknown is null, never implied zero |
| `currency` | TEXT, nullable | Currency attached to that amount |
| `source` | TEXT | Existing accounting cost-source classification |
| `rate_json` | TEXT, JSON | Frozen allowed rate snapshot, or JSON null |
| `calculation_version` | INTEGER | 1: current pricing/estimation algorithm |
| `recorded_at` | TEXT | UTC write time, not provider billing time |

Revision 1 supersedes revision 0 for totals. Do not sum revisions. An imported terminal
record creates revision 1 only; there is no invented initial estimate. Finished accounting
is not overwritten by a repeated finalization. Future bill corrections need explicit new
revision semantics, not mutation of an earlier charge. The current `requests` projection
provides the authoritative selected accounting result used by existing queries.

### requests compatibility view

`requests(id, started, record)` selects rows from `llm_requests` with
`accounting_enabled=1`. Existing read-only consumers can query this view. There are no
write triggers; new writes must use the store API so normalized facts stay consistent.

## Metric boundaries and aggregation

All following metrics are version 1 in microseconds:

| Metric | Start | End |
| --- | --- | --- |
| `dictation.start_wait` | Accepted start, before foreground-window/recorder setup | First audio callback or already-ready stream |
| `microphone.wake` | Resume requested before dispatching hardware work | First callback of the newly opened stream |
| `dictation.transcribe_wait` | Accepted stop | Final ASR response accepted by the client, before result-processing queue |
| `dictation.result_queue` | Final ASR received | Final-result handler entered after waiting for audio upload cleanup |
| `llm.prepare` | Eligible LLM action entered | Provider transport method called |
| `llm.request` | Provider transport method called | Method returns or raises, including response validation |
| `llm.total` | Eligible LLM action entered | Terminal observation before accounting finalization and caller output |
| `dictation.output_prepare` | LLM service returned, including disabled/skip/fallback | Insertion called or explicitly skipped; includes formatting and enabled archive wait |
| `dictation.insert` | Text output called | Text output returns |
| `dictation.post_stop` | Accepted stop | Insertion returns, or output is explicitly skipped |
| `dictation.udp` | UDP broadcast called | Broadcast returns |
| `dictation.auto_enter` | Application-specific enter delay begins | Enter call returns or focus/exit guard skips it |
| `llm.stage.configuration` | Application phase entered | Phase left |
| `llm.stage.preparation` | Application phase entered | Phase left |
| `llm.stage.credentials` | Application phase entered | Phase left |
| `llm.stage.client_setup` | Application phase entered | Phase left |
| `llm.stage.awaiting_headers` | Application phase entered | Phase left |
| `llm.stage.body_read` | Application phase entered | Phase left |
| `llm.stage.json_parse` | Application phase entered | Phase left |
| `llm.stage.response_validation` | Application phase entered | Phase left |
| `llm.stage.custom_transport` | Injected transport phase entered | Phase left |

LLM stage values sum repeated visits to the same application phase, so they have no
invented start/end offsets. These phases overlap the separate preparation/request/total
metrics. HTTP headers are not first-token latency. Client timing does not measure pure
provider inference. A completed preparation remains successful when a later request
fails. Request outcome describes the transport method; action total describes the whole
action, so cancellation after a successful response does not relabel transport success.
Earlier application phases remain completed; the terminal phase carries the action result.
Failed/cancelled stage durations terminate at failure/cancellation.
If a required end boundary was never reached, any saved elapsed-to-failure value carries
the unsuccessful outcome and is excluded from success distributions.

The Statistics dashboard selects local calendar windows (today, this week, this month,
this year, seven or thirty days, including today), converted to half-open UTC boundaries.
Weeks start on Monday; month and year windows start on their first calendar day.
Tasks use their start time;
accounting uses request start time and saved text uses archive dates. Runtime queries
read at most 10,000 tasks and 200,000 measurements in one read transaction with a
two-second execution budget. The compatibility query without a window retains its
latest-500-task scope. Each metric displays at most 32 provider/model/version groups;
details contain the latest 50 tasks and at most 128 measurements per task. Reaching
these caps is explicit and never changes collection or retention.

Median, arithmetic mean, maximum and nearest-rank P95 use the same successful-operation
cohort; fewer than twenty observations
receive a small-sample marker. Metric versions and LLM provider/model groups remain
separate. Unsuccessful stages, not-applicable and missing measurements are separate
counts. A successful early stage remains eligible when a later stage fails. Overall
durations are measured independently; there is no stacked sum of overlapping stages.
Each group additionally exposes its latest recorded observation, including its
availability, outcome, task ID and task start time. Recency follows descending task
start order, then operation end/start offset within a task; offsets from different
runs are never compared. This observation can be unsuccessful or not applicable and
does not silently fall back to an earlier success. Empty cohorts have null aggregates,
not zero; genuine observed zero durations remain valid. Mean/max/recent values are
query projections requiring no schema or metric-version migration.
Overview duration cards use version-one cohorts, emphasize the latest observation,
and retain all four aggregates and sample counts below it. Run write-error/drop counters cover
entire sessions overlapping the selected period, not event-time daily counts. Pending
tasks do not prove that their process remains alive. Cost and history queries retain
their provenance/currency distinctions and adjacent-month timezone handling.

## Legacy compatibility and import

New accounting writes target only the unified database. Reads prefer unified requests
and supplement them with unimported version-one monthly ledger rows. Matching request IDs
are counted once. Old records never create invented dictation identities or new metric
values. Bounded queries explicitly report truncation and malformed/unsupported records.

`scripts/activity_records.py --directory <directory> --import-legacy` imports one month
per transaction. It reads each source database without modifying it, validates allowed
version-one fields, preserves original request identities, and stores source hashes.
Reimport is idempotent; differing existing identities abort that month's transaction.
Earlier committed months remain imported and re-running safely resumes. Unknown fields
are rejected instead of copying arbitrary content. The tool reports counts without
printing record bodies. The caller must select the intended directory; importing does
not change which directory the application uses.

Do not run an old writer concurrently against legacy files during cutover. A changed
source record with an already-imported identity requires review; it is not silently
applied over the unified record. Old monthly files remain available as read-only backups.

## Exact SQL schema

The following SQL is the complete executable version-one schema, including defaults,
checks, indexes, unique constraints, foreign keys and the compatibility view.

<!-- schema-sql -->

```sql
CREATE TABLE IF NOT EXISTS schema_migrations (
 version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL, checksum TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS runs (
 run_id TEXT PRIMARY KEY NOT NULL, component TEXT NOT NULL, app_version TEXT NOT NULL,
 started_at TEXT NOT NULL, ended_at TEXT, clock TEXT NOT NULL,
 resolution_ns INTEGER NOT NULL CHECK(resolution_ns>0), dropped INTEGER NOT NULL DEFAULT 0 CHECK(dropped>=0),
 write_errors INTEGER NOT NULL DEFAULT 0 CHECK(write_errors>=0));
CREATE TABLE IF NOT EXISTS tasks (
 task_id TEXT PRIMARY KEY NOT NULL, run_id TEXT NOT NULL REFERENCES runs(run_id),
 started_at TEXT NOT NULL, stopped_at TEXT, ended_at TEXT,
 state TEXT NOT NULL CHECK(state IN ('running','finished','interrupted')),
 outcome TEXT, error_code TEXT, complete INTEGER NOT NULL DEFAULT 1 CHECK(complete IN (0,1)));
CREATE INDEX IF NOT EXISTS tasks_started ON tasks(started_at);
CREATE TABLE IF NOT EXISTS operations (
 operation_id TEXT PRIMARY KEY NOT NULL, task_id TEXT REFERENCES tasks(task_id),
 run_id TEXT NOT NULL REFERENCES runs(run_id), parent_id TEXT REFERENCES operations(operation_id),
 kind TEXT NOT NULL, started_offset_us INTEGER CHECK(started_offset_us>=0),
 ended_offset_us INTEGER CHECK(ended_offset_us>=started_offset_us), outcome TEXT NOT NULL,
 error_code TEXT, UNIQUE(task_id, kind, operation_id));
CREATE INDEX IF NOT EXISTS operations_task ON operations(task_id);
CREATE TABLE IF NOT EXISTS metric_definitions (
 name TEXT NOT NULL, version INTEGER NOT NULL, unit TEXT NOT NULL,
 start_boundary TEXT NOT NULL, end_boundary TEXT NOT NULL, aggregation TEXT NOT NULL,
 PRIMARY KEY(name,version));
CREATE TABLE IF NOT EXISTS measurements (
 measurement_id TEXT PRIMARY KEY NOT NULL, operation_id TEXT NOT NULL REFERENCES operations(operation_id),
 metric TEXT NOT NULL, metric_version INTEGER NOT NULL, value_us INTEGER CHECK(value_us>=0),
 availability TEXT NOT NULL CHECK(availability IN ('observed','not_applicable','not_collected','lost')),
 source TEXT NOT NULL CHECK(source IN ('client','server','legacy')),
 method_version INTEGER NOT NULL CHECK(method_version>0), reason TEXT,
 FOREIGN KEY(metric,metric_version) REFERENCES metric_definitions(name,version),
 CHECK((availability='observed' AND value_us IS NOT NULL) OR
       (availability!='observed' AND value_us IS NULL)),
 UNIQUE(operation_id,metric,metric_version));
CREATE INDEX IF NOT EXISTS measurements_metric ON measurements(metric,metric_version);
CREATE TABLE IF NOT EXISTS asr_bindings (
 binding_id TEXT PRIMARY KEY NOT NULL, task_id TEXT NOT NULL REFERENCES tasks(task_id),
 connection_id TEXT NOT NULL, server_run_id TEXT NOT NULL, socket_id TEXT NOT NULL,
 wire_task_id TEXT NOT NULL, UNIQUE(server_run_id,socket_id,wire_task_id));
CREATE TABLE IF NOT EXISTS llm_requests (
 id TEXT PRIMARY KEY NOT NULL, operation_id TEXT REFERENCES operations(operation_id),
 started TEXT NOT NULL, month TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('unfinished','completed','failed','cancelled','not_sent','skipped')),
 accounting_enabled INTEGER NOT NULL CHECK(accounting_enabled IN (0,1)),
 record TEXT NOT NULL CHECK(json_valid(record)),
 origin TEXT NOT NULL DEFAULT 'native', legacy_hash TEXT);
CREATE INDEX IF NOT EXISTS llm_requests_month ON llm_requests(month,started,id);
CREATE TABLE IF NOT EXISTS llm_usage (
 request_id TEXT NOT NULL REFERENCES llm_requests(id), category TEXT NOT NULL,
 source TEXT NOT NULL CHECK(source IN ('provider','estimate')),
 quantity INTEGER NOT NULL CHECK(quantity>=0), valid INTEGER NOT NULL CHECK(valid IN (0,1)),
 method TEXT, PRIMARY KEY(request_id,category,source));
CREATE TABLE IF NOT EXISTS llm_costs (
 request_id TEXT NOT NULL REFERENCES llm_requests(id), revision INTEGER NOT NULL,
 amount TEXT, currency TEXT, source TEXT NOT NULL, rate_json TEXT CHECK(json_valid(rate_json)),
 calculation_version INTEGER NOT NULL, recorded_at TEXT NOT NULL,
 PRIMARY KEY(request_id,revision));
CREATE VIEW IF NOT EXISTS requests AS
 SELECT id,started,record FROM llm_requests WHERE accounting_enabled=1;
```
