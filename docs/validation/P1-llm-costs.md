# P1 LLM cost estimates and alerts

Accepted by the user on 2026-09-22 with commit authorized after removing cost popups. Cost summaries remain in diagnostics; budget alerts remain in the console and diagnostics. No cost notification creates a desktop window. The separate legacy Toast removal is recorded in its own commit.

## Implementation scope

Content-free monthly SQLite records track each selected LLM attempt before dispatch and update its terminal outcome. Money returned by a documented endpoint or explicitly configured total-charge field takes priority over rate estimates. Exact endpoint/model rate snapshots preserve historical amounts. Unknown prices and incomplete output remain explicit; currencies are never combined. Transactional threshold claims deduplicate alerts across concurrent requests, processes, and restarts.

The default Google rate was checked against its official pricing page on 2026-09-22. OpenRouter total-cost semantics and currency were checked against its official accounting/support documentation. The [user guide](../user/llm-costs.md) owns source links, configuration, uncertainty semantics, and commands.

## Automated evidence

- Project interpreter: Python 3.11.15 in the existing `capswriter` environment.
- Final full default suite: 579 passed, 8 deselected; configured coverage 85.22%.
- Focused coverage includes reported zero/nonzero money, invalid metadata, cache/reasoning arithmetic, missing/expired rates, custom endpoint assumptions, failed responses with usage, timeout/cancel/missing-key outcomes, pending crash records, month rollover, rate reload snapshots, currency separation, concurrent/persistent threshold deduplication, disabled controls, private-content exclusion, disk failure, and read-only JSON reporting.
- Full syntax compilation (including existing local configurations), Ruff, configured mypy targets, internal-language/documentation checks, and `git diff --check` passed. The CLI Chinese help and JSON query were checked. Existing user edits and private configuration were preserved.
- Pytest used a fresh workspace-local `--basetemp` with the cache provider disabled because the environment's existing temporary/cache directories were not writable; no permission or dependency changes were needed.
- No real provider request, microphone input, key simulation, model download, or full package build was performed. Mocked packaging verifies the public cost template is copied without local cost settings or ledgers.

## Manual acceptance

1. Restart the source client to load the implementation. Confirm the existing provider, model, presets, and private settings remain intact.
2. Run a short normal LLM correction/translation. Confirm text behavior is unchanged and no cost popup appears. Run `python scripts/llm_costs.py --details` and verify one completed row with model, usage, amount/source, and rate snapshot. Compare provider-returned usage or money if available.
3. Cancel an in-flight request with the existing cancel key. Confirm original-text handling is unchanged and the row is explicitly cancelled/incomplete, with reported usage or a labelled possible-cost scenario.
4. With an explicitly chosen invalid test key or endpoint, verify failure feedback and a failed/not-sent row. Restore the existing setting afterward. Do not publish private responses or credentials.
5. Copy the public cost template only if no local cost file exists. Temporarily set a low positive USD threshold, run a request, and confirm a console/diagnostic alert occurs once. Repeat and restart the client: the same monthly threshold must not alert again. Restore desired thresholds.
6. Disable budget alerts and request summaries independently. Disable accounting and verify no new accounting rows appear, while text processing and existing archives keep their independent behavior.
7. Confirm cost processing creates no floating notification. Existing recording and preparation indicators keep their own presentation.
8. Run the monthly query and JSON detail export. Confirm reported, estimated, possible, and unknown costs remain distinguishable. Native account billing may differ due to free tiers, taxes, outside usage, or unreturned costs.

Cross-month, concurrent, expired-rate, disk-failure, and abrupt-interruption paths are covered synthetically; no destructive interruption of a live client is required for acceptance. The user accepted the feature after requesting removal of its popup. Automated checks do not certify every real provider response or desktop/DPI configuration.

## Separate legacy component removal

The user explicitly authorized deleting the legacy notification component in a second commit. Seven Toast modules, their exports, obsolete locale/demo resources and dedicated rendering dependencies were removed. Recording/preparation still use the existing `recording_indicator` renderer; only its shared thread queue moved to `StatusUIHost`. No cost popup remains.

Post-removal validation: 584 default tests passed, 8 deselected; configured coverage 85.22%. Mock tests cover callback queuing before root readiness, bounded batches, callback/startup failures, owner-thread root cleanup, concurrent first use, and imports without `markdown` or `tkhtmlview`. Syntax, Ruff, configured mypy, language/documentation and diff checks passed. Explicit application shutdown/join integration and actual monitor/DPI interaction remain within their existing validation scope; this removal does not claim to fix monitor-following behavior.
