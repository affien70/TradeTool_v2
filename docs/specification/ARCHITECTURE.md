# Architecture

## Goal

TradeTool v2 must be a modular Streamlit application with clear boundaries
between runtime product code, research and training code, artifact promotion,
reports, persistence, and UI.

The design must be architecture-driven and evidence-driven. It must not hardcode
old Momentum or the current ML model as the permanent winner.

## Runtime Shape

Use native Streamlit Pages plus modular packages.

```text
app.py
pages/
  1_Screener.py
  2_Holdings.py
  3_Diagnostics.py
  4_Config.py

tradetool/
  config/
  runtime/
  universe/
  data/
  features/
  ranking/
  policy/
  explanation/
  holdings/
  artifacts/
  diagnostics/
  ui/
  utils/
```

## Architectural Boundaries

### Runtime Application

Owns:

- universe selection
- coverage validation
- eligibility construction
- ranking execution
- practical trade policy
- candidate typing
- explanation rendering
- manual workflow support
- Holdings integration

Must not import research or training modules as required runtime dependencies.

### Research, Training, and Backtesting

Own:

- backtests
- walk-forward and OOF experiments
- challenger training runs
- comparison reports
- ad hoc research diagnostics

They may read production-style contracts, but runtime must not depend on them.

### Artifact Promotion

Owns:

- artifact identity
- checksums
- metadata validation
- incumbent vs challenger registration
- approved production selection
- rollback mapping

This responsibility must exist explicitly in module design, even if initially
implemented with simple files.

### Reports and Diagnostics

Own:

- append-only diagnostic outputs
- coverage reports
- baseline vs challenger comparisons
- focus-ticker sanity sets
- production run summaries

Reports belong under `reports/`. Production runtime must not depend on report
side-state.

### Persistence

Owns:

- SQLite access
- settings reads and writes
- Holdings persistence
- cached market data access

The existing database is reused initially. No schema change is bundled into the
v2 specification baseline.

### UI

Owns:

- Streamlit page orchestration
- table and chart display
- Norwegian display labels
- selection state

UI must not contain authoritative policy logic.

## Production Flow Mapping

The one production flow maps to architecture like this:

```text
universe selection
-> coverage validation
-> feature and eligibility build
-> baseline or challenger ranking
-> practical trade policy
-> candidate classification
-> explanation and risk rendering
-> manual decision
-> Holdings tracking
```

## Safety Rules

- candidate classification must be independent from Streamlit
- ranking and trade policy are separate concepts
- raw score, practical signal, and candidate type must not be conflated
- research code must not be a required import path for runtime
- training code must not be imported into the product runtime
