# Code Review Report — 043 Conversation ID Header

Reviewer: `code-review-agent` (no main-agent context). Scope: `3e9fe42..HEAD` (`f94816b`, User-Agent) plus the
uncommitted conversation-id work. Checks run by reviewer: `pytest tests/unit` 1697 passed; ruff, ruff format, `mypy src`
clean; changed modules 97–100% covered.

## Round 1 — verdict: approved with minor suggestions (no blocker/major)

Verified OK: resolution never fails a run; header == stored id for printable-ASCII names; encoding cannot raise;
resolve-once across retries; on-demand runs share `execute_run` and clear contextvars; CR/LF cannot split headers;
caller `X-CONVERSATION-ID`/`User-Agent` stripped case-insensitively; no secrets logged; layer rules and conventions.

| ID | Severity | Finding | Resolution |
|----|----------|---------|------------|
| M1 | minor | Very long `display_name` (no `max_length`) → `X-CONVERSATION-ID` > 8 KiB → proxy/DIAL Core 400/431 → a previously successful run fails (conflicts with SC-004) | Fixed: `resolve_conversation_location` raises `ConversationCreationError` when the encoded id exceeds `MAX_CONVERSATION_ID_HEADER_BYTES` (4096) → existing no-header / skip path; tests added |
| M2 | minor | `CONFIGURATION.md` properties table rows for `extra_headers` / `create_conversation` not updated (FR-015) | Fixed: both rows describe the header, precedence and failure behaviour, linking to the detailed bullet |
| M3 | minor | `documentation/properties.md` line endings CRLF → LF (whole-file diff) | Fixed: restored CRLF |
| N1 | nit | Trace-id test exercised the processor on a hand-built dict only | Fixed: replaced by a `test_jobs.py` test running `execute_run` inside a span through the real `_add_trace_span_ids` processor |
| N2 | nit | Launcher test name overstated what it proves; redundant `is … or ==` assertion | Fixed: test renamed/docstring clarified; assertion reduced to `==` |
| N3 | nit | Docs named only the bucket as failure cause; "equals run's `conversation_id`" not true for failed runs | Fixed: wording generalised; failed runs noted as carrying the header but recording no `conversation_id` |
| N4 | nit | Unexpected resolver error logged without traceback | Fixed: non-`ConversationCreationError` failures log the same event with `exc_info=True` |
| N5 | nit | "application vs model" check duplicated | Fixed: `deployment_kind()` helper in `src/worker/conversation.py` |

## Status

All findings addressed; no open issues.
