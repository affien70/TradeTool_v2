# Holdings Do Not Touch Gate

## Rule

Do not touch Holdings / Beholdninger during screener UI recovery.

Holdings can be changed only when a measured compatibility defect is found and the user approves a narrow fix.

## Protected area

Protected files and modules include, but are not limited to:

- `pages/beholdning.py`
- `src/tradetool/holdings/`
- Holdings contracts under `src/tradetool/contracts/`
- any future holdings UI adapter
- any holdings persistence or database migration

The legacy V1 Holdings implementation in `/Users/affien/DEV/TradeTool/holdings_view.py` is reference evidence only. It must not be copied, rewritten, or changed as part of screener UI recovery.

## Protected behavior

The existing v2 specification already protects:

- transaction import behavior
- deduplication and update semantics
- active position calculation
- cost-basis confidence handling
- benchmark usage
- HOLD, SELL, and FOLG MED semantics
- scheduled holdings report semantics
- email payload semantics

## Allowed during screener UI recovery

- Read legacy Holdings code as reference.
- Read v2 Holdings specification.
- Add documentation describing the protection rule.

## Not allowed during screener UI recovery

- No Holdings UI redesign.
- No Holdings signal change.
- No Holdings database write/migration.
- No Holdings import/export change.
- No shared signal derivation change.
- No opportunistic cleanup.

## Stop condition

Stop immediately if a screener UI change appears to require Holdings behavior changes. Report the dependency instead of patching Holdings.
