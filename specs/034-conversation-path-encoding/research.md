# Phase 0 Research: Conversation Path Encoding

**Feature**: 034-conversation-path-encoding | **Date**: 2026-08-06

All unknowns from the Technical Context are resolved below. Two of the findings (R-002, R-003)
materially change the shape of the implementation and were not visible from the spec alone.

---

## R-001: Does httpx double-encode a path that already contains percent-escapes?

**Decision**: No special handling needed. Build the resource path with percent-escapes already in
place and pass it to `DialClient.put_conversation` unchanged. `DialClient` needs no modification.

**Rationale**: Verified empirically against the project's pinned httpx in this repo:

```text
input segment : applications%2Fbucket1%2Fmy%20app__Daily%20Report%20%2F%20Sync__runid
httpx raw_path: /v1/conversations/BUCKET/.scheduler/sid/applications%2Fbucket1%2Fmy%20app__Daily%20Report%20%2F%20Sync__runid
```

`httpx.URL` recognises existing valid `%XX` sequences and preserves them rather than re-quoting the
`%`. `raw_path` — the bytes actually put on the wire — is byte-identical to the input. Note that the
convenience property `URL.path` shows a *decoded* view (`applications/bucket1/my app...`); this is a
display artifact only and must not be mistaken for double-decoding. Tests asserting wire behaviour
should assert on `raw_path`, never on `path`.

**Alternatives considered**: Passing pre-split segments and letting the client join and quote them —
rejected as a needless widening of `DialClient`'s contract (Simplicity/YAGNI) when the transport
already behaves correctly.

---

## R-002: `quote()` alone does not keep the name segments unambiguously separable (FR-008)

**Decision**: Encode each variable part with `quote(value, safe="")` **and then additionally
percent-encode the underscore**: `.replace("_", "%5F")`.

**Rationale**: This is the finding that matters most, and it is not obvious from the spec.

The conversation name is three parts joined by `CONVERSATION_NAME_SEPARATOR` (`__`). Underscore is an
*unreserved* character in RFC 3986, so `quote(v, safe="")` deliberately leaves it alone. Real
application deployment ids in this project's own test fixtures already contain a double underscore:

```text
applications/bkt/Untitled app 1101__0.0.1
```

Encoded with plain `quote(..., safe="")` the resulting name would be:

```text
applications%2Fbkt%2FUntitled%20app%201101__0.0.1__My%20Schedule__<run-id>
```

Splitting that on `__` yields four parts, not three — the deployment id has silently injected a
separator. FR-008 (parts must remain unambiguously separable) and FR-007 (must decode back to the
exact original) are both violated. A schedule named `a__b` causes the same defect.

Encoding `_` as `%5F` makes it structurally impossible for `__` to appear inside any encoded part, so
`name.split("__")` is exact for all inputs, and `unquote` still returns the original string
character-for-character. The cost is cosmetic: underscores in names render as `%5F`. Since the
confirmed decision (spec FR-009) is that the display name is already the encoded segment and users
will see percent-escapes regardless, this costs nothing beyond what is already accepted.

**Alternatives considered**:

- *Change the separator to something `quote` always encodes* (e.g. `/`, space): would change the
  naming convention for every existing conversation reader and break continuity with the 031 feature's
  established `__` convention. Rejected.
- *Split from the right with `rsplit("__", 2)`*: fixes the run-id and schedule-name boundary but still
  mis-splits when the *schedule name* contains `__`. Partial fix; rejected as fragile.
- *Reject or sanitise names containing `__`*: user-visible behaviour change not asked for, and would
  break the existing `Untitled app 1101__0.0.1` application id. Rejected.

---

## R-003: Segment length cap — keep, or drop?

**Decision**: Drop the cap. Remove `CONVERSATION_NAME_SEGMENT_MAX_LENGTH` and do not truncate
anything. Over-long paths surface as a `ConversationCreationError`, which the existing caller already
logs and swallows.

**Rationale**: The existing 64-character cap was applied to the slug (`slugify(..., max_length=64)`)
and existed for slug aesthetics. It cannot survive this feature:

- Truncating an *encoded* string can cut a `%XX` escape in half, producing an undecodable segment.
- Truncating the *raw* string before encoding is lossy and directly contradicts FR-007, which requires
  decoding to yield the exact original name.

So truncation is incompatible with the feature's central guarantee. The spec's edge-case note already
sanctions the alternative branch — "fail conversation creation in a way that is logged and does not
fail the schedule run itself" — and `src/worker/jobs.py:81-89` already implements exactly that: any
exception from `create_run_conversation` is caught, logged as `conversation_create_failed`, and does
not alter the run outcome. FR-011 is therefore satisfied with no new code.

**Residual risk (accepted, flagged)**: `ScheduleRequest.display_name` has *no* `max_length` validator
(`src/lib/schemas/schedule.py:137-149` — only a non-empty check). A pathologically long schedule name
will now produce a proportionally long path and a failed (but non-fatal, logged) conversation write.
Adding `max_length` to `display_name` is the right fix, but it is an API-contract change to the
schedule endpoints and is **out of scope for this feature** — recorded here as a follow-up.

**Alternatives considered**: Encode, then truncate on an escape boundary — keeps paths short but
silently produces a name that no longer decodes to the schedule name, defeating SC-003. Rejected.

---

## R-004: Which identifier goes where (FR-003 vs FR-010)

**Decision**: Carry **both** identifiers through to the builder as two distinct fields.

| Value | Source | Used for |
|---|---|---|
| `deployment_id` | `properties.payload["model"]` — verbatim, as configured on the schedule | the storage path / conversation name (encoded) |
| `model_id` | application `reference` for applications; the deployment id itself for models | `conversation.model.id` and each message's `model.id` |

**Rationale**: These two requirements look contradictory but are not, and the clarification round
confirmed the split: FR-003 bans the underlying model reference from the *storage location*; FR-010
requires keeping it *inside the document* so reopening the conversation behaves exactly as today
(SC-007). Consequently the `GET /v1/applications/{id}` lookup in
`src/worker/conversation.py:166` **must be retained** — a plausible-looking "simplification" that
deletes it would regress FR-010.

`ConversationBuildInput` therefore gains a `deployment_id` field alongside the existing `model_id`.
For model-backed schedules the two values are equal; for application-backed schedules they differ.

**Alternatives considered**: Reusing the single `model_id` field for both roles — impossible, since
the two values genuinely differ for applications. Rejected.

---

## R-005: Do `conversation.id` / `folderId` stay derived from the encoded path?

**Decision**: Yes — keep deriving them from the same resource path string, unchanged in mechanism.
They will now naturally contain the encoded final segment.

**Rationale**: `build_conversation` already computes `conversation_id = f"conversations/{resource_path}"`
and `folder_id = conversation_id.rsplit("/", 1)[0]`. This preserves the existing invariant, asserted by
`tests/unit/worker/test_conversation.py:142` (`test_id_final_segment_matches_name`), that the final
path segment equals the conversation name — which is also precisely what FR-009 mandates. Because the
encoding never emits a bare `/` inside the final segment, the `rsplit("/", 1)` that derives `folderId`
remains correct. No structural change; only the segment's content changes.

---

## R-007: Server-side preservation of percent-escapes — OPEN, gates merge

**Status**: **Unresolved.** Cannot be settled offline. Raised by code review as C1.

**The gap**: R-001 proves that *httpx* transmits percent-escapes intact. It proves nothing about what
DIAL Core does on receipt. The spec's Assumptions section asserts that "the underlying conversation
storage accepts percent-encoded segments and preserves them byte-for-byte" — that is an assumption,
not a verified fact, and the entire feature rests on it.

**Why it matters more than a normal assumption**: all three failure modes are silent.

1. DIAL decodes the segment before resolving the resource — `applications%2Fbkt%2Fapp` becomes
   `applications/bkt/app`, recreating the exact multi-level split that FR-004 and US2 exist to
   prevent.
2. DIAL rejects a name that decodes to contain `/` — every application-backed run then logs
   `conversation_create_failed` and continues, because FR-011 deliberately swallows the error. The
   run still reports success.
3. DIAL derives stored identity from the decoded URL path but persists the body's `id`/`folderId`
   verbatim (both carry the *encoded* name) — path and id diverge and the conversation may not open,
   regressing SC-007, the one thing this feature promises not to disturb.

In every case the scheduler reports success. **Absence of complaints is not evidence here.**

**Required before merge**: run the application case in
`tests/e2e/1.3-Create-chat-completion-schedule-oauth-with-conversation-creation.http` against a real
DIAL, with an `{{application_id}}` containing `/`, a space, and `__`. Confirm: (a) the `GET` returns
200 at the encoded path; (b) the `.scheduler/{schedule_id}` folder holds one entry, not a nested
tree; (c) the conversation opens in DIAL Chat. Record the outcome here.

**If DIAL does decode**: percent-encoding is the wrong scheme, the contract's 2.0.0 version needs
revisiting, and an encoding that survives a decode pass (or a different separator strategy) is
required. That would be a design change, not a patch.

---

## R-006: Removing the `slugify` dependency

**Decision**: Remove the `python-slugify>=8.0.4` dependency from `pyproject.toml`.

**Rationale**: `src/worker/conversation.py:4` is the only import of `slugify` anywhere in `src/` or
`tests/`. Once FR-006 removes slugification, the dependency is dead weight. Dropping it satisfies the
constitution's dependency-hygiene expectation and shrinks the CVE surface.

**Verification required during implementation**: re-run the grep before deleting the dependency, and
run `uv sync` + full test suite afterwards to confirm nothing else resolved it transitively.
