# TradeTool v2 Specification

This folder contains the documentation-only v2 specification for TradeTool.
It defines what must be approved before implementation starts. It does not
change source code, tests, database state, model artifacts, market data,
Holdings behavior, deployment files, or Git branches.

## Product Goal

TradeTool v2 is a small, reliable, explainable private stock screener for:

- Norway/OSE
- S&P 500

The goal is to rank plausible buy candidates better than the current app and
at least compete with professional stock-picking or trading experts. The app
is decision support, not an automated trader and not an AI oracle.

## Strategic Position

- Old `main` Momentum is a required reproducible baseline, not the predetermined
  final production engine.
- The current ML model is an incumbent or challenger input, not a production-
  approved winner and not discarded.
- v2 must not predetermine the winning ranking engine.

Required process:

```text
build clean baseline
-> build controlled ML challenger
-> compare on identical data, universes, dates and costs
-> promote only the objectively better robust solution
```

Raw ML ranking must never be the unrestricted user-facing buy list.

## Document Set

The approved v2 specification consists of eleven documents:

1. [PRODUCT_REQUIREMENTS.md](PRODUCT_REQUIREMENTS.md)
2. [ARCHITECTURE.md](ARCHITECTURE.md)
3. [MODULE_MAP.md](MODULE_MAP.md)
4. [SCREENER_POLICY.md](SCREENER_POLICY.md)
5. [HOLDINGS_POLICY.md](HOLDINGS_POLICY.md)
6. [ML_STRATEGY.md](ML_STRATEGY.md)
7. [MIGRATION_PLAN.md](MIGRATION_PLAN.md)
8. [TEST_AND_DECISION_GATES.md](TEST_AND_DECISION_GATES.md)
9. [DATA_CONTRACTS.md](DATA_CONTRACTS.md)
10. [DEFINITION_OF_DONE.md](DEFINITION_OF_DONE.md)
11. This README

## Approval Process

Approval is complete only when:

1. all eleven documents exist,
2. cross-document terminology is consistent,
3. no document predetermines Momentum or ML as the final winner,
4. one production screener flow is locked,
5. data contracts and decision gates are explicit,
6. migration phases are approved in order,
7. remaining open decisions are listed rather than guessed.

If a future implementation task cannot point to the relevant v2 document,
phase, and gate, the specification must be updated first.
