# Quickstart & Validation: User Identity Authentication

This guide validates that schedule and schedule-run endpoints resolve the caller identity from
DIAL and scope all operations to it. It references [contracts/](./contracts/) and
[data-model.md](./data-model.md) for details rather than repeating them.

## Prerequisites

- `uv sync` completed
- Environment configured: `DIAL_URL`, `DIAL_API_KEY` (and DB settings). Never commit/read
  `.env` per the constitution.
- Optional: `USER_ID_PATH_LIST` to override the default `["userClaims.mail.0", "project"]`.

## Quality gates (run from repo root)

```bash
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy src
uv run pytest --cov=src --cov-report=term-missing
```

All must pass. New code (`auth.py`, `dial_client.py`, `user_id.py`, `user_id_path_list`) must
be covered.

## Unit validation scenarios

1. **Path extraction** (`tests/unit/lib/utils/test_user_id.py`)
   - first path hit, fallback to second path, no path resolves (`None`), whitespace/empty
     skipped, integer index vs dict key, index out of range / type mismatch skipped.
2. **DIAL client** (`tests/unit/lib/clients/test_dial_client.py`) — mock httpx:
   - 2xx JSON → dict; 401/403 → `UserIdNotResolvedError`; 5xx → `DialServiceError`; transport
     error → `DialServiceError`; non-JSON 2xx → `{}`; assert `Api-Key` value never logged.
3. **`get_user_id` dependency** (`tests/unit/api/dependencies/test_auth.py`):
   - success returns id; missing key → 401; DIAL reject → 401; no identity → 401; DIAL outage →
     502; service not called on any rejection.

## Endpoint validation (manual / TestClient)

With the dependency overridden (or DIAL mocked) so `GET /v1/info` returns
`{"userClaims": {"mail": ["alice@example.com"]}}`:

```bash
# Authenticated create — owner becomes alice@example.com (not mock_user_id)
curl -X POST http://localhost:5000/v1/schedules/ \
  -H "Api-Key: alice-key" -H "Content-Type: application/json" \
  -d '{"display_name":"S","trigger":{"cron":{"fields":{"hour":9}}},
       "properties":{"target_type":"chat_completion","url":"https://core.dial/.../chat/completions"}}'
# → 201, body.created_by == "alice@example.com"

# Missing Api-Key → 401, no row created
curl -i -X GET http://localhost:5000/v1/schedules/

# A different identity (bob) lists schedules → does NOT see alice's schedule
curl -X GET http://localhost:5000/v1/schedules/ -H "Api-Key: bob-key"
```

## Expected outcomes (maps to spec Success Criteria)

- **SC-001**: every endpoint operates under the DIAL-resolved id; `MOCK_USER_ID` no longer
  referenced by `schedules.py` / `runs.py`.
- **SC-002**: missing/invalid/unresolvable identity → request rejected (401), no data touched.
- **SC-003**: two identities see disjoint schedules/runs (integration test
  `tests/integration/test_user_id_auth.py`).
- **SC-004**: when `userClaims.mail.0` is absent, identity falls back to `project`.

## Done checklist

- [ ] `user_id_path_list` added to settings with documented default
- [ ] `extract_user_id`, `DialClient`, `get_user_id` implemented and unit-tested first (Red→Green)
- [ ] All six endpoints inject `UserIdDep`; `MOCK_USER_ID` removed from endpoint modules
- [ ] Existing endpoint unit tests updated to override `get_user_id`
- [ ] Integration test proves per-identity isolation
- [ ] All quality gates green
