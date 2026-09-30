"""Immutable version-one storage and metric contracts."""

VERSION = 1

# name: (start boundary, end boundary). All measurements are local monotonic durations.
METRICS = {
    'dictation.start_wait': ('accepted_start', 'first_capture_block_or_already_ready'),
    'microphone.wake': ('resume_requested', 'first_callback_of_new_stream'),
    'dictation.transcribe_wait': ('accepted_stop', 'final_asr_received'),
    'dictation.result_queue': ('final_asr_received', 'final_result_processing_started'),
    'llm.prepare': ('action_entered', 'transport_called'),
    'llm.request': ('transport_called', 'transport_returned_or_raised'),
    'llm.total': ('action_entered', 'action_terminal_before_accounting_finish'),
    'dictation.output_prepare': ('llm_returned', 'insertion_called_or_skipped'),
    'dictation.insert': ('insertion_called', 'insertion_returned_or_raised'),
    'dictation.post_stop': ('accepted_stop', 'insertion_returned_or_output_skipped'),
    'dictation.udp': ('udp_called', 'udp_returned_or_raised'),
    'dictation.auto_enter': ('auto_enter_delay_started', 'auto_enter_returned_or_skipped'),
}
for _phase in ('configuration', 'preparation', 'credentials', 'client_setup',
               'awaiting_headers', 'body_read', 'json_parse', 'response_validation', 'custom_transport'):
    METRICS['llm.stage.' + _phase] = ('application_phase_entered', 'application_phase_left')

DDL = """
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
"""
