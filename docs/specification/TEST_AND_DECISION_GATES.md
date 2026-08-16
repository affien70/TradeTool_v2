# Test and Decision Gates

## Purpose

TradeTool v2 must prevent architecture drift and policy patch loops. Baseline
and challenger decisions must be evidenced, reproducible, and reviewable.

## Screener Gates

Every screener or ranking change must verify:

- identical universe definition
- identical market-data dates
- explicit coverage counts
- explicit eligibility counts
- deterministic rejection counts by reason
- focus-ticker sanity
- candidate-type correctness
- explanation correctness

## Baseline vs Challenger Comparison

Every comparison must use:

- identical universes
- identical dates
- identical cost assumptions
- identical benchmark handling
- identical Top N definitions

The output must include:

- baseline results
- challenger results
- coverage report
- focus-ticker results
- rejection-reason summary
- recommendation

## Runtime Sanity Gates

Live or local runtime sanity must verify:

- input universe count
- valid ticker count
- market-data coverage count
- enough-history count
- feature-complete count
- eligible count
- ranked count
- rejection counts by reason
- latest data-date distribution

## ML Promotion Gates

ML promotion requires clear improvement over the baseline in:

- top-N candidate plausibility
- top-vs-bottom future return spread
- drawdown
- stability across periods
- stability across universes
- turnover and costs
- explanation correctness
- focus-ticker sanity
- reject-reason correctness

## Holdings Gates

Any Holdings-adjacent migration must verify:

- active position parity
- cost-basis parity
- signal parity
- scheduled report parity
- email payload parity where applicable

## Full Regression Gate

Before a requested code promotion or production change:

```bash
PYTHONPATH=. .venv/bin/python -m unittest discover -s tests -p 'test_*.py'
```

## Decision Outcomes

Every comparison or gate review must end with exactly one of:

- promote
- keep as diagnostic
- park
- revise
- revert

## Stop Criteria

Stop if:

- coverage is unclear
- historical semantics are unverifiable
- explanation or rejection behavior is inconsistent
- the winner depends on hand-picked examples only
- runtime depends on research scripts
- rollback is undefined
