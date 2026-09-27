# LLM diagnostic granularity

Implemented and accepted by the user for commit on 2026-09-27, after inspection of one user-driven successful provider request. Acceptance covers the implementation and evidence below; broader live failure/desktop checks remain pending. This record covers the first item of the [desktop workflow plan](../../TODO.md#desktop-workflow-and-diagnostic-detail), not the later configuration, prompt, GUI or branding work.

## Outcome

Added task/request correlation independent of content logging, a process-scoped configuration revision, application stages and available HTTPX transport timings, response-header observations, byte/parse states, validated token usage and one terminal observation per eligible LLM action. Failure observations retain bounded native exception chains and distinguish missing metadata from filtered or unobserved metadata. Error excerpts preserve useful unknown provider details through the existing opt-in content channel, with credential redaction and context gating. Toast classification now also distinguishes explicit payment/balance, DNS resolution and connection refusal failures.

The transport still sends one request, does not retry or follow redirects, and retains existing timeouts, response-size limits, cancellation, original-text fallback and independent accounting. The task caller passes its ID into the LLM service; no wire or process ownership change is required. A narrow `.gitignore` exception makes the new production diagnostic module visible despite the existing `diag*.py` scratch-file rule.

Field definitions, availability, controls and limits have one home in the [logging contract](../reference/logging-and-records.md#llm-diagnostic-fields-and-granularity). [User instructions](../user/logs-and-records.md) explain reading an error after its hint disappears. Local configuration and provider files were not edited. No new switch or configuration-version migration is required.

## Automated evidence

Environment: the existing `capswriter` interpreter, Python 3.11.15. Tests use synthetic content and `httpx.MockTransport`; no microphone, global keys, models, GPU commands or live provider requests were used.

| Check | Result |
| --- | --- |
| Default test suite | 727 passed, 8 deselected; 18.92 seconds |
| Ruff configured source scope | Passed |
| Configured mypy targets | Passed, 7 source files |
| Compileall source/template/tests/scripts and existing local configurations | Passed |
| Internal language and documentation checks | Passed |
| Diff whitespace and scope review | Passed |

New boundary cases cover success with cost tracking disabled; task/request and concurrent-request isolation; configuration failure, disabled actions, no selected preset and missing keys; native DNS/TLS/refusal chains; unknown provider codes; payment versus balance versus quota/rate limits; partial bodies, malformed JSON, oversized bodies and unusable output; timeout/cancellation cleanup; trace field allowlisting and event caps; exception cycles; configuration revision changes; sanitized error content with text/context combinations; environment-key whitespace; non-JSON authentication lines; tree/excerpt limits; reader content permissions; and observation-sink failure without changing successful output. Existing LLM, privacy, accounting, output and lifecycle tests are included in the default run.

Configured coverage targets are protocol/server merging/text tools rather than the changed LLM modules; an additional coverage-only run was not used as evidence for this feature. Test files are visible to Git without force-adding them. New temporary test/cache data stays under ignored `.cache` directories.

## Observation overhead

A local synthetic benchmark constructed 300 observations, each with configuration fingerprinting, 7 application transitions, 10 supported transport callbacks and a terminal event. Median producer CPU time was 0.545 ms and p95 was 0.706 ms. The logger had no file handler, and there was no HTTP/provider work. This is a bounded metadata-path measurement, not end-to-end latency or a disk-throughput guarantee. Error text sanitization was not included in this timing. Trace emission is capped at 32 events per request, cause traversal at 8 exceptions and error-tree traversal at 128 nodes; diagnostic writes retain the existing bounded background queue.

## Remaining manual scope

- The user restarted and exercised normal dictation; one real successful request is covered below. Other service-specific response/header formats and native failures remain unverified. No automated paid test call was made.
- Check the localized status hint for a naturally occurring provider failure and confirm the original-text behavior. Windows focus/DPI behavior was not exercised by these mock tests.
- The two reported connection failures cannot be reconstructed from old logs: they did not retain a complete native cause chain. Their exact cause remains unresolved; no billing diagnosis is asserted. A future failure can provide the new fields.
- HTTPX trace availability depends on the transport/version. Mock callbacks validate observation behavior, not all real socket paths. Missing trace events are explicitly reported rather than fabricated.
- Logs remain best-effort queued diagnostics. Queue overflow, process termination and file failure can lose events; one terminal emission attempt does not guarantee durable delivery.

The user accepted this diagnostic outcome and authorized commit on 2026-09-27. Placeholder reference capture and diagnostic timing precision remain separate open follow-ups; their fixes are not part of this acceptance.

## Subsequent user-driven success

On 2026-09-27, the user requested inspection of a sentence produced through dictation with LLM assistance. Local client/server logs and the single matching accounting row showed a 35.10-second recording, approximately 2.207 seconds of server processing, and a successful LLM action measured at 1,562 ms. The response was HTTP 200, HTTP/1.1, JSON, 648 decoded bytes, with complete reading and successful parsing. Supported TCP/TLS/send/receive-header traces were present without trace-cap omissions. Provider-reported usage was consistent: 1,693 input and 37 output tokens, 1,730 total. The accounting record matched the request and used a stored rate estimate rather than a provider-reported charge. Raw text, reference content, request IDs and local log paths are deliberately omitted from this tracked record.

This confirms real success-path observability after restart, not universal model correctness or native failure classification. The LLM changed the transcript but left an acronym inaccurate relative to the user's submitted message; the logs do not establish how the later message was edited. The captured reference also contained input placeholder text rather than meaningful surrounding user text, which needs a separate caret-compatibility fix.

Timing granularity is a confirmed limitation: this Python 3.11.15 Windows interpreter reports a 15.625 ms `GetTickCount64` resolution for `monotonic()`. Several fast stages therefore appear as zero; their exact durations cannot be reconstructed. `perf_counter()` reports a 100 ns nominal `QueryPerformanceCounter` resolution and is a candidate for diagnostic-only timing. Both findings are recorded separately in [TODO](../../TODO.md#p1--audited-hardening); no runtime logic was changed during this inspection.
