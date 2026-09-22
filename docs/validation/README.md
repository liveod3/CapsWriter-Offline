# Validation records

Use these records to distinguish implementation checks from user acceptance. Results apply only to their stated date, configuration, and scope. They do not certify all hardware, models, providers, or release artifacts.

## Accepted outcomes

| Outcome | Record |
| --- | --- |
| Internal English and documentation reorganization | [P1 internal English](P1-internal-english.md) |
| Connection/task isolation | [P0-01](P0-01-connection-task-isolation.md) |
| Recording ownership | [P0-02a](P0-02a-recording-ownership.md) |
| Audio lifecycle | [P0-02b](P0-02b-audio-lifecycle.md) |
| Bounded terminal outcomes | [P0-03](P0-03-terminal-outcomes.md) |
| Diagnostic privacy | [P0-04](P0-04-diagnostic-privacy.md) |
| Recording storage and configuration reload | [P1 storage/reload](P1-recording-storage-config-reload.md) |
| Multilingual interface | [P1 localization](P1-multilingual-interface.md) |

## Supporting historical stages

Accepted accounting outcome: [P1 LLM cost estimates and alerts](P1-llm-costs.md).

The terminal-outcomes item has one final acceptance record. Its earlier [file lifecycle](P0-03a-file-task-lifecycle.md), [task errors](P0-03b-task-error-outcomes.md), [microphone deadlines](P0-03c-microphone-deadlines.md), and [result delivery](P0-03d-server-result-delivery.md) records remain evidence of individual slices, not separate current acceptance gates.

Add new results to the relevant outcome rather than copying the full test matrix. [AGENTS.md](../../AGENTS.md#verify-the-change) owns routine verification; [TODO.md](../../TODO.md) owns active status.
