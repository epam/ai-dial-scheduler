# Contract: Scheduler Conversation Path & Name

**Feature**: 034-conversation-path-encoding | **Version**: 2.0.0 | **Date**: 2026-08-06

This is an *outbound* contract: the scheduler is the producer, DIAL conversation storage is the store,
and any reader of `.scheduler` conversations is the consumer. The scheduler's own REST API is
unchanged by this feature — no OpenAPI change, no new endpoint, no request/response schema change.

**Version 2.0.0 is a breaking change** to the naming convention introduced in feature 031. Names
produced before and after this change are not interconvertible. Existing conversations are not
migrated (FR-012); readers must tolerate both forms.

---

## Encoding function

Every variable part is encoded with:

```text
encode(v) = percent_encode(v, safe = "") then replace "_" with "%5F"
```

- `safe = ""` means every character outside RFC 3986 *unreserved* is percent-encoded, including `/`.
  The unreserved set is `A-Z a-z 0-9 - . _ ~`, so `-`, `.`, and `~` stay literal in output (`_` does
  not — it is encoded by the extra step below). A part of `.` or `..` therefore encodes to itself;
  that is inert here only because the assembled name always contains the `__` separators, so the
  final segment can never *equal* `.` or `..`.
- The extra `_` → `%5F` step guarantees the literal sequence `__` can never occur inside an encoded
  part, which is what keeps the joined name unambiguously splittable. See research R-002.
- `decode(v) = percent_decode(v)` — standard, requires no special casing, and is an exact inverse.

**Guarantee**: `decode(encode(v)) == v` for every string `v`, including empty, non-ASCII, and
separator-bearing values.

**⚠️ Reimplementing `encode` in another language**: this rule is Python's
`quote(v, safe="")`, which is *not* the same as JavaScript's `encodeURIComponent`.
`encodeURIComponent` leaves `!`, `'`, `(`, `)`, and `*` literal; `quote(safe="")` percent-encodes
all five. A JS consumer must add
`.replace(/[!'()*]/g, c => "%" + c.charCodeAt(0).toString(16).toUpperCase())` before the underscore
step, or it will compute a path that was never written and get a 404 that looks like a product bug.
Reference vector, pinned by a unit test:

```text
input:   a!'()*b~-._
correct: a%21%27%28%29%2Ab~-.%5F
```

---

## Conversation name

```text
name = encode(deployment_id) ++ "__" ++ encode(schedule_display_name) ++ "__" ++ encode(run_id)
```

- `deployment_id` — the deployment configured on the schedule (`payload.model`), verbatim. For
  applications this is the `applications/...` id, **never** the resolved `reference` (FR-003).
- `schedule_display_name` — the schedule's name, verbatim, never slugified (FR-006).
- `run_id` — a UUID. Encoding is the identity function on it, so no name changes byte-for-byte;
  it is encoded anyway so the three-part guarantee does not depend on the shape of a value
  produced elsewhere.

**Parsing**: `name.split("__")` returns exactly three parts for every input. Apply `decode` to the
first two.

---

## Storage path

```text
resource_path = "{bucket}/.scheduler/{schedule_id}/{name}"
document id   = "conversations/" ++ resource_path
folderId      = "conversations/{bucket}/.scheduler/{schedule_id}"
PUT target    = {dial_url}/v1/conversations/{resource_path}
```

`bucket` and `schedule_id` are not re-encoded — they are already path-safe (bucket id, UUID). The
final segment contains no bare `/`, so `folderId` remains derivable by `rsplit("/", 1)`.

The path is handed to `DialClient.put_conversation` with its escapes already in place. httpx
preserves them byte-for-byte on the wire (research R-001); the client is not modified.

---

## Document body

Unchanged by this feature, and deliberately so:

| Field | Value |
|---|---|
| `name` | the encoded name above — byte-identical to the final path segment (FR-009) |
| `id` / `folderId` | as above |
| `model.id` | the resolved model **reference** for applications, the deployment id for models (FR-010) |
| everything else | unchanged |

`model.id` is the one place the application reference legitimately survives. Reopening an
application-backed conversation therefore behaves exactly as before (SC-007).

---

## Worked examples

Model-backed, simple name:

```text
deployment_id : gpt-4o
display_name  : Daily Report
name          : gpt-4o__Daily%20Report__018f...c3
model.id      : gpt-4o
```

Application-backed, separator and spaces in the id:

```text
deployment_id : applications/bkt/Untitled app 1101__0.0.1
display_name  : Weekly Sync
name          : applications%2Fbkt%2FUntitled%20app%201101%5F%5F0.0.1__Weekly%20Sync__018f...c3
model.id      : c9e5decf-1284-421e-ab84-c790beea837d
```

Non-Latin name (previously transliterated to `privet`, now preserved):

```text
display_name  : Привет
encoded       : %D0%9F%D1%80%D0%B8%D0%B2%D0%B5%D1%82
```

Name containing the separator and a slash:

```text
display_name  : a__b/c
encoded       : a%5F%5Fb%2Fc
split("__")   : still exactly 3 parts
```

---

## Consumer notes

- Names are **not** human-readable when they contain special characters; percent-escapes are expected
  and are a deliberate, confirmed decision (FR-009). Consumers wanting a readable label should
  `decode` the first two parts themselves.
- Do not assume a length bound on the name. The previous 64-character cap is removed (research R-003);
  no truncation is performed.
- Do not assume `model.id` matches the deployment part of the name — for applications it will not.
