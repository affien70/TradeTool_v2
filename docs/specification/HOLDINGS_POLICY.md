# Holdings Policy

## Goal

Holdings remains a protected module. The v2 screener work must not quietly
change Holdings semantics.

## Guardrails

- existing behavior is characterized before porting
- existing database is reused without schema change initially
- shared signal derivation remains authoritative
- GUI, export, scheduled report, and email must not diverge
- no Holdings redesign is bundled with screener work

## Protected Behavior

The migration must preserve:

- transaction import behavior
- deduplication and update semantics
- active position calculation
- cost-basis confidence handling
- benchmark usage
- HOLD, SELL, and FOLG MED semantics
- scheduled holdings report semantics
- email payload semantics

## Shared Signal Rule

Holdings signal derivation must remain single-source and be reused by:

- Streamlit UI
- exports
- scheduled report
- email report
- regression tests

## Database Rule

The existing SQLite schema is reused initially. Any schema change requires:

- explicit migration task
- backup plan
- rollback plan
- migration tests
- compatibility verification
- approval before implementation

## Migration Rule

Holdings is integrated after the screener flow, contracts, and comparison logic
are stable enough to avoid accidental semantic drift.
