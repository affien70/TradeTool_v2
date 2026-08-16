# Module Map

This document maps v2 responsibilities to modules. It is a target design, not
an instruction to build everything at once.

## `tradetool.config`

Responsibilities:

- runtime defaults
- path resolution
- environment checks
- policy version identifiers
- active artifact references

Must not:

- fetch market data
- compute features
- implement screener or Holdings policy

## `tradetool.universe`

Responsibilities:

- universe definitions
- ticker normalization
- reproducible universe membership snapshots
- valid ticker filtering
- universe diagnostics

## `tradetool.data`

Responsibilities:

- SQLite access
- price-history contract reads and writes
- benchmark-history reads
- settings persistence
- Holdings persistence

## `tradetool.features`

Responsibilities:

- deterministic feature construction
- benchmark alignment
- trend, momentum, RS, liquidity, drawdown, volatility, stretch inputs
- feature completeness checks

Rules:

- vectorized and deterministic
- no Streamlit imports
- no ranking policy labels

## `tradetool.runtime`

Responsibilities:

- orchestration of the one production flow
- build candidate universe for a run
- attach coverage and eligibility state
- call ranking and policy layers
- return typed runtime results

## `tradetool.ranking`

Suggested responsibilities:

- clean baseline ranking engine
- challenger ranking adapter
- shared ranked-candidate contract
- identical-input comparison hooks

Rules:

- old Momentum is a baseline, not a predetermined final winner
- current ML is a challenger or incumbent input, not automatically secondary
- ranking output is upstream of trade policy

## `tradetool.policy`

Suggested responsibilities:

- eligibility filters
- tradability and liquidity checks
- practical execution policy
- candidate type assignment
- trade signal assignment
- deterministic rejection reasons

Rules:

- no Streamlit imports
- ownership cannot reduce ranking, Top N inclusion, practical score, or BUY
  eligibility
- model score cannot rescue an ineligible candidate

## `tradetool.explanation`

Responsibilities:

- candidate explanations
- rejection explanations
- risk explanation payloads
- Norwegian label mapping if separated from core enums

## `tradetool.holdings`

Responsibilities:

- transaction import
- position calculation
- shared Holdings signal derivation
- scheduled report payloads
- compatibility with the existing database

Rules:

- existing behavior characterized before porting
- no schema change in the initial phases
- GUI, export, scheduled report, and email must not diverge

## `tradetool.artifacts`

Responsibilities:

- artifact metadata
- checksum validation
- production-approved artifact registry
- incumbent and challenger identities
- promotion and rollback references

## `tradetool.diagnostics`

Responsibilities:

- coverage reports
- baseline vs challenger comparisons
- focus-ticker sanity sets
- invariant summaries
- live runtime sanity exports

## `tradetool.ui`

Responsibilities:

- Streamlit table configs
- display-only helpers
- charts
- layout wrappers
- Norwegian presentation adapters

## `tradetool.utils`

Responsibilities:

- dates
- numeric coercion
- formatting
- light logging helpers

Rules:

- must not become a dumping ground for domain logic
