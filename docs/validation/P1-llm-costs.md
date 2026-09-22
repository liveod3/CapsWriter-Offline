# P1 LLM cost records and queries

## Request-path simplification (2026-09-22)

At the user's request, remove automatic cost summaries, budget alerts, their locale resources and configuration controls. Keep durable per-request accounting and the read-only monthly query script. Request preparation inserts its pending row; completion updates only that row in a transaction. Monthly aggregation runs only when explicitly querying records. HTTP client lifetime is outside this change.

Existing rates, ledger paths and request rows are preserved. Retired `show_summary`, `alerts_enabled` and `budgets` settings in local files are ignored. Existing `alerts` tables remain untouched for data preservation; new ledgers no longer create them. The query script retains its JSON fields, currency separation, model grouping and detail export.

Validation on Python 3.11.15: 124 focused tests passed; the full default suite passed with 587 tests and 8 deselected. Syntax compilation, Ruff, internal-language/documentation checks and `git diff --check` passed. Regression coverage denies SQL SELECT during request writes, preserves old request/alert rows, accepts retired local settings without losing rates or directory choices, and verifies concurrent writes, currency-separated queries, cancellation/failure and disabled accounting.

Synthetic local completion measurements (five samples per size) were 7.1/6.9/7.9 ms median with 1/1,000/10,000 monthly records, compared with the earlier 6.7/24.0/175.0 ms inspection. These measure ledger completion only, not provider/network latency; runs are illustrative rather than a controlled end-to-end benchmark. No private ledger was read and no live provider or desktop check was run. The unchanged HTTP connection path remains outside this change.

Accepted by the user on 2026-09-22 with commit authorized. Earlier acceptance and test counts below describe the original feature.

## Original acceptance evidence

The original feature was accepted by the user on 2026-09-22 with commit authorized after removing cost popups. At that acceptance, cost summaries remained in diagnostics and budget alerts in the console and diagnostics; these are removed by the simplification above. No cost notification creates a desktop window. The separate legacy Toast removal is recorded in its own commit.

### Original implementation scope

Content-free monthly SQLite records track each selected LLM attempt before dispatch and update its terminal outcome. Money returned by a documented endpoint or explicitly configured total-charge field takes priority over rate estimates. Exact endpoint/model rate snapshots preserve historical amounts. Unknown prices and incomplete output remain explicit; currencies are never combined. Transactional threshold claims deduplicate alerts across concurrent requests, processes, and restarts.

The default Google rate was checked against its official pricing page on 2026-09-22. OpenRouter total-cost semantics and currency were checked against its official accounting/support documentation. The [user guide](../user/llm-costs.md) owns source links, configuration, uncertainty semantics, and commands.

### Original automated evidence

- Project interpreter: Python 3.11.15 in the existing `capswriter` environment.
- Final full default suite: 579 passed, 8 deselected; configured coverage 85.22%.
- Focused coverage includes reported zero/nonzero money, invalid metadata, cache/reasoning arithmetic, missing/expired rates, custom endpoint assumptions, failed responses with usage, timeout/cancel/missing-key outcomes, pending crash records, month rollover, rate reload snapshots, currency separation, concurrent/persistent threshold deduplication, disabled controls, private-content exclusion, disk failure, and read-only JSON reporting.
- Full syntax compilation (including existing local configurations), Ruff, configured mypy targets, internal-language/documentation checks, and `git diff --check` passed. The CLI Chinese help and JSON query were checked. Existing user edits and private configuration were preserved.
- Pytest used a fresh workspace-local `--basetemp` with the cache provider disabled because the environment's existing temporary/cache directories were not writable; no permission or dependency changes were needed.
- No real provider request, microphone input, key simulation, model download, or full package build was performed. Mocked packaging verifies the public cost template is copied without local cost settings or ledgers.

## Manual checks for the simplified behavior

1. Restart the source client to load the implementation. Confirm the existing provider, model, presets, and private settings remain intact.
2. Run a short normal LLM correction/translation. Confirm text behavior is unchanged and no cost popup appears. Run `python scripts/llm_costs.py --details` and verify one completed row with model, usage, amount/source, and rate snapshot. Compare provider-returned usage or money if available.
3. Cancel an in-flight request with the existing cancel key. Confirm original-text handling is unchanged and the row is explicitly cancelled/incomplete, with reported usage or a labelled possible-cost scenario.
4. With an explicitly chosen invalid test key or endpoint, verify failure feedback and a failed/not-sent row. Restore the existing setting afterward. Do not publish private responses or credentials.
5. Confirm normal successful requests emit no automatic cost summary or budget alert. Existing recording and preparation indicators keep their own presentation.
6. Disable accounting and verify no new accounting rows appear, while text processing and existing archives keep their independent behavior. Restore the desired accounting setting.
7. If local settings contain retired summary/alert fields, confirm rates and the ledger directory still apply; those retired fields have no effect.
8. Run the monthly query and JSON detail export. Confirm reported, estimated, possible, and unknown costs remain distinguishable. Native account billing may differ due to free tiers, taxes, outside usage, or unreturned costs.

Cross-month, concurrent, expired-rate, disk-failure, and abrupt-interruption paths are covered synthetically; no destructive interruption of a live client is required for acceptance. The user accepted the feature after requesting removal of its popup. Automated checks do not certify every real provider response or desktop/DPI configuration.

## Separate legacy component removal

The user explicitly authorized deleting the legacy notification component in a second commit. Seven Toast modules, their exports, obsolete locale/demo resources and dedicated rendering dependencies were removed. Recording/preparation still use the existing `recording_indicator` renderer; only its shared thread queue moved to `StatusUIHost`. No cost popup remains.

Post-removal validation: 584 default tests passed, 8 deselected; configured coverage 85.22%. Mock tests cover callback queuing before root readiness, bounded batches, callback/startup failures, owner-thread root cleanup, concurrent first use, and imports without `markdown` or `tkhtmlview`. Syntax, Ruff, configured mypy, language/documentation and diff checks passed. Explicit application shutdown/join integration and actual monitor/DPI interaction remain within their existing validation scope; this removal does not claim to fix monitor-following behavior.
