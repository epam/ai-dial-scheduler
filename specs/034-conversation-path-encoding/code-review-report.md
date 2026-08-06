# Code Review Report — 034 Conversation Path Encoding

- **spec-dir**: `specs/034-conversation-path-encoding/`
- **Branch**: `034-conversation-path-encoding` (uncommitted working tree)
- **Date**: 2026-08-06
- **Mode**: Verification (round 2) — first pass was a Fresh Review on the same date
- **Artifacts consulted**: `spec.md`, `plan.md`, `research.md` (incl. new R-007), `tasks.md`, `contracts/conversation-path.md`, `CLAUDE.md`, `.specify/memory/constitution.md`

## Summary

**Approved, subject to C1.** Every issue from round 1 is Resolved or correctly declined with a
sound rationale; two were addressed beyond what was asked. The Python implementation is in good
shape and I found no new defects in it. Verification did surface two new items, both outside `src/`:
**N1** (the e2e file's JavaScript `encode` is not equivalent to the Python one for `!'()*`) and
**N2** (formatting residue). Neither blocks merge; N1 should be fixed before anyone relies on that
`.http` file, which is precisely the file C1 depends on.

C1 remains Open by necessity, not by neglect — it is now recorded as `research.md` R-007, marked
gating, with the failure modes and the verification procedure. That is the right disposition.

**Gates re-run independently**: `uv run pytest` → 721 passed, 110 skipped; `ruff check src tests` →
clean; `ruff format --check src tests` → 159 files already formatted; `mypy src` → clean. All match
the coordinator's report.

### Status roll-up

| | Count | Ids |
|---|---|---|
| Resolved | 10 | I1, I2, I3, I4, I5, I6, I7, M1, M2, M5 |
| Declined (accepted) | 1 | M3 |
| Open | 4 | C1 (gating, external verification), N1, N2, N3 |

Round-1 M4 folded into M2 / the contract edits and is confirmed done.

---

## Critical Issues

### C1 — Server-side preservation of percent-escapes is unverified — Status: Open (correctly tracked, gates merge)

**Where**: `src/worker/conversation.py:96-105`; now documented as `research.md` R-007.

Unchanged in substance — this cannot be closed without a live DIAL. What changed is that it is no
longer an unrecorded assumption: R-007 states the gap explicitly, marks itself unresolved and
merge-gating, enumerates all three silent failure modes (decode-and-split, reject-and-swallow,
id/path divergence), specifies the verification steps, and records that a negative result is a
design change rather than a patch. It also preserves the key point that the scheduler reports
success in every failure mode, so absence of complaints proves nothing.

**Remaining action**: run the application case in
`tests/e2e/1.3-Create-chat-completion-schedule-oauth-with-conversation-creation.http` against a real
DIAL with an `{{application_id}}` containing `/`, a space, and `__`; record the outcome in R-007.
**Fix N1 first** — the `.http` file is the instrument for this check, and its current `encode`
would build a mismatched path for any id containing `!'()*`, producing a 404 that reads like a
product failure.

---

## New Findings (this round)

### N1 — The e2e `encode` is not equivalent to the Python one — Status: Open

**Where**: `tests/e2e/1.3-Create-chat-completion-schedule-oauth-with-conversation-creation.http:59`
and `:144`

```js
// "encode() is encodeURIComponent plus "_" -> "%5F""
const encode = (v) => encodeURIComponent(v).replace(/_/g, "%5F");
```

The comment asserts an equivalence that does not hold. `encodeURIComponent` leaves `!`, `'`, `(`,
`)`, and `*` literal; Python's `quote(v, safe="")` percent-encodes all five. Verified:

```text
input:  a!'()*b~-._
python: a%21%27%28%29%2Ab~-.%5F
js:     a!'()*b~-.%5F
```

So for any deployment id or schedule name containing those characters — `Untitled app (1)`,
`Don't`, `app*v2` are all realistic — the `.http` file computes a path the scheduler never wrote,
and the `GET` 404s. The failure looks like the feature is broken when it is the test harness that
is wrong, and it would land exactly during the C1 investigation, when a false negative is most
expensive.

**Fix**:

```js
// encodeURIComponent leaves !'()* literal; Python's quote(safe="") does not. Encode them too,
// or this file computes a path the scheduler never wrote.
const encode = (v) =>
    encodeURIComponent(v)
        .replace(/[!'()*]/g, (c) => "%" + c.charCodeAt(0).toString(16).toUpperCase())
        .replace(/_/g, "%5F");
```

Worth adding the same caveat to `contracts/conversation-path.md`, since the contract invites
consumers to reimplement `encode`, and `encodeURIComponent` is the obvious first reach for anyone
doing so in JavaScript. This is the "reimplementation drifts" risk from round-1 I2 showing up for
real — though note that relocating the Python helper would not have prevented it, which is further
support for the decision recorded under I2.

### N2 — Formatting residue from the import hoist — Status: Open (trivial)

**Where**: `tests/unit/worker/test_conversation.py:486` and `:503`

Both tests now open with a blank line where the in-function import used to be:

```python
    async def test_application_deployment_resolves_reference(self, mocker, mock_dial):

        mock_dial.get_application.return_value = ...
```

`ruff format` tolerates a single leading blank line in a body, so the gate does not catch it. Delete
both.

### N3 — `conversation_resource_path` docstring not updated for I1 — Status: Open (trivial)

**Where**: `src/worker/conversation.py:98-104`

Still reads "path-safe because the deployment id and display name are percent-encoded". After I1 all
three parts are encoded. `_conversation_name`'s docstring was updated correctly; this one was
missed. Say "because every part is percent-encoded".

---

## Round-1 Issues — Verification

### I1 — Encode the run id — Status: Resolved

`_conversation_name` (`src/worker/conversation.py:81-91`) now encodes all three parts. Confirmed no
byte-level change to existing names: `encode_name_part` is the identity function on a UUID and on
the tests' `run-conv-001`, and the full suite passes with unchanged expected strings. The docstring
explains both the identity property and the reason for doing it anyway. Contract line 40 updated to
`encode(run_id)` with a matching note at line 46.

### I2 — Helper visibility — Status: Resolved (relocation and `decode` correctly declined)

Renamed to `encode_name_part` (`:57`); tests import the public name. The decision not to move it to
`src/lib/utils/naming.py` and not to add `decode_name_part` is **right, and I withdraw the
suggestion**. Constitution V is the correct authority: there are zero in-repo consumers for either,
`urllib.parse.unquote` is a real and documented inverse, and — as N1 demonstrates — the drift that
worried me occurs in a JavaScript file that a Python relocation could never have protected. The
public name gets the actual benefit (tests stop reaching past an underscore) at no structural cost.
The right mitigation for the underlying risk is the contract caveat in N1, not a module move.

### I3 — Dead line — Status: Resolved

`mock_dial.get_by_id = None` is gone from `test_written_path_is_encoded_and_single_segment`.

### I4 — Edge-case coverage — Status: Resolved (exceeded)

All four suggested cases are in the parametrize list, plus `(".", ".")`. More valuably,
`test_final_segment_is_never_a_traversal_token` now pins the invariant I flagged as only
incidentally safe: it asserts the leaf is never `.` or `..` and always contains
`CONVERSATION_NAME_SEPARATOR`, with a comment explaining *why* `quote` leaving `.` alone is
survivable. That converts an accident into a checked property, which is what I was after.

The `("a%5Fb", "%2F%2E%2E")` case correctly exercises the double-encoding path — re-derived:
`encode("a%5Fb")` → `a%255Fb` → `unquote` → `a%5Fb`. Exact.

### I5 — Untyped path-facing payload value — Status: Resolved

`src/worker/conversation.py:180-184` now guards with `isinstance(deployment, str)` and raises a
named error, with a comment stating why the check exists. Covered by
`test_non_string_model_raises_named_conversation_error` over `int`/`list`/`dict`/`""`. The test's
`object.__setattr__` bypass matches the existing `test_missing_model_raises_conversation_error`
immediately below it, so it is consistent with local convention rather than a new hack. (Nit, not
worth a finding: `""` is a string, so it sits slightly oddly under a "non_string" test name — the
assertion is still correct, since the message covers non-empty.)

### I6 — Import placement — Status: Resolved; the in-function exception is correct

`ConversationCreationError`, `RunContext`, `ScheduleProperties` hoisted in `test_jobs.py`;
`ApplicationDetails` hoisted in `test_conversation.py`.

**On the question asked**: keep `from src.worker.jobs import ...` in-function. I verified the claim
— there are 40 in-function imports of `src.worker.jobs` in that file and not one at module level.
That is unambiguously deliberate (the module has import-time side effects that fixtures need to
front-run), and a single hoisted import would both break the pattern and risk import-order coupling
that no test currently has. Matching the file convention is the right call.

### I7 — Double derivation of the resource path — Status: Resolved

`CONVERSATION_ID_PREFIX` added to `constants.py` with an explanatory comment, used in
`build_conversation` (`:146`); `create_run_conversation` now derives via
`conversation.id.removeprefix(CONVERSATION_ID_PREFIX)` (`:218-220`) with a comment explaining the
anti-drift intent. Exactly one derivation, and the PUT target is now provably the stored id.

### M1 / M2 / M4 — Status: Resolved

`build_conversation`'s Args entry now distinguishes path-facing from document-facing ids; the stale
`(FR-013)` is gone from `src/worker/jobs.py:76`; the contract carries the unreserved-characters
note.

### M3 — Duplicated `encode` in the `.http` file — Status: Declined, accepted

The rationale holds: JetBrains HTTP-client script blocks genuinely do not share scope, and the
global-plus-eval workaround is worse than a duplicated one-liner. The added comment at line 58
explains it for the next reader. **However** — the duplication is now load-bearing for N1: whoever
fixes that must fix it in both places (lines 59 and 144).

### M5 — Stale `__pycache__` — Status: Resolved

`src/lib/utils/__pycache__/naming.*.pyc` and the matching test artefact are gone.

---

## Intent Alignment

No divergences. The FR-by-FR table from round 1 still holds; two entries strengthened:

- **FR-008** — now guaranteed unconditionally rather than contingent on `run_id` being UUID-shaped
  (I1), and the guarantee is pinned by a test that no longer depends on the shape of any externally
  produced value.
- **FR-011** — the named-error guard (I5) improves diagnosability of the swallowed-failure path
  without changing its behaviour.

R-007's addition to `research.md` is a genuine improvement to the artefact set: the spec previously
carried the byte-preservation claim in its Assumptions section, where an unverified assumption is
indistinguishable from a settled one. It is now explicit, attributed, and gating.

The R-003 residual risk (`ScheduleRequest.display_name` has no `max_length`) remains open and out of
scope, correctly recorded. Still worth a follow-up ticket rather than research prose alone.

---

## Checklist Pass

| Area | Result |
|---|---|
| Correctness & logic | Pass — encoding guarantees re-verified after the run-id change |
| Edge cases | Pass — round-1 gaps closed; the traversal invariant is now asserted, not assumed |
| Type safety | Pass — `mypy src` clean; the round-1 `Any` leak is guarded |
| Style & conventions | Pass — one formatting nit (N2) |
| Architecture & design | Pass — single derivation of the resource path; helper kept where it is used, per Constitution V |
| Database & ORM | N/A |
| Project structure | Pass |
| Testing | Pass — 721 passed / 110 skipped; new tests assert invariants rather than restating the implementation |
| Error handling | Pass |
| Security | Pass — traversal explicitly pinned inert; no secrets logged |
| Observability | Pass |
| Dependency hygiene | Pass |
| E2E harness | **Fails N1** — the JS encoder is not equivalent to the Python one |
