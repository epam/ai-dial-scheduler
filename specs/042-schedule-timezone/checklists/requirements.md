# Specification Quality Checklist: Per-Schedule Timezone

**Purpose**: Validate specification completeness and quality before proceeding to planning
**Created**: 2026-11-04
**Feature**: [spec.md](../spec.md)

## Content Quality

- [x] No implementation details (languages, frameworks, APIs)
- [x] Focused on user value and business needs
- [x] Written for non-technical stakeholders
- [x] All mandatory sections completed

## Requirement Completeness

- [x] No [NEEDS CLARIFICATION] markers remain
- [x] Requirements are testable and unambiguous
- [x] Success criteria are measurable
- [x] Success criteria are technology-agnostic (no implementation details)
- [x] All acceptance scenarios are defined
- [x] Edge cases are identified
- [x] Scope is clearly bounded
- [x] Dependencies and assumptions identified

## Feature Readiness

- [x] All functional requirements have clear acceptance criteria
- [x] User scenarios cover primary flows
- [x] Feature meets measurable outcomes defined in Success Criteria
- [x] No implementation details leak into specification

## Notes

- `Intl.supportedValuesOf('timeZone')` and the IANA Time Zone Database are named because they define the external contract with the frontend. They are not implementation choices.
- FR-015 names documentation artifacts. This is a deliverable requirement, not an implementation detail.
- Resolved in planning (see [research.md](../research.md)): a top-level `timezone` field with a new `schedules.timezone` column (R1, R2); every identifier in the IANA database is accepted (R3); `tzdata` is added as a direct dependency (R3). Originally left to `/speckit-plan`:
  - Where the field sits in the API (top-level `timezone` on the schedule or `trigger.timezone`) and where it is stored (a new column with a migration, or inside the existing trigger JSON). Both satisfy FR-002 and FR-010.
  - Whether identifiers outside the browser set that the timezone database still knows (`EST`, `CET`, `US/Eastern`, `Etc/GMT+2`) are accepted or rejected. FR-006 sets only the minimum accepted set.
  - How to supply the timezone database independently of the host OS, for example the `tzdata` package.
- Pre-planning findings from 2026-11-04, measured on APScheduler 3.11.2:
  - Every one of the 418 browser timezones is accepted by `CronTrigger`, both on macOS and in the `python:3.14-alpine` image.
  - On macOS, `europe/kyiv` is accepted because the file system ignores letter case. In Alpine it is rejected. FR-007 requires consistent behaviour.
  - Spring forward: daily 02:30 `Europe/Berlin` fires at 03:30 local. This already matches FR-012.
  - Fall back: daily 02:30 `Europe/Berlin` fires **twice** (`02:30+02:00` and `02:30+01:00`). This violates FR-013, so the plan needs a trigger-level fix. The same applies to `EntryCronTrigger` inside `OrTrigger`.
  - Fall back: an hourly entry fires at both 02:00 occurrences. This matches FR-013.
