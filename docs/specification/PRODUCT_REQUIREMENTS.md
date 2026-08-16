# Product Requirements

## Product Goal

TradeTool v2 is a private stock screener for Norway/OSE and the S&P 500. It
must help the user rank plausible buy candidates, understand why names are
eligible or rejected, and make manual decisions with higher confidence.

The app is decision support, not an automated trading system.

## Locked Production Flow

There is one user-facing production flow:

```text
select universe
-> validate data coverage
-> build eligible candidate set
-> rank candidates
-> apply practical trade policy
-> assign candidate type and trade signal
-> show explanation and risk
-> manual decision
-> track in Holdings
```

Runtime research comparisons may remain separate, but v2 must not expose
competing user-facing Momentum, practical-first, and ML screener products.

## Strategic Requirements

- Old `main` Momentum is a reproducible baseline and comparison method.
- The current ML model is an incumbent or challenger input.
- v2 must not predetermine the final production ranking engine.
- Raw ML ranking must never be the unrestricted user-facing buy list.
- ML may become the production ranking engine later only if it clearly passes
  the promotion gates in the specification.

## Users

The primary user is an active private investor who:

- wants a daily shortlist for manual review,
- values explainability over black-box outputs,
- cares about liquidity, trend, relative strength, momentum, drawdown, and
  stretch,
- wants Norway/OSE and S&P 500 support,
- needs Holdings continuity and consistent reports.

## Core Runtime Inputs

Initial production inputs:

- universe membership
- data freshness and coverage
- price
- volume
- liquidity and tradability
- trend and moving averages
- relative strength
- 1m, 3m, 6m, and 12m momentum
- volatility
- drawdown
- stretch
- benchmark context

Not in the initial production core:

- CAPEX
- SEC EDGAR
- Future Demand Score
- news
- estimate revisions
- short data
- broad fundamental expansion
- US_EXTENDED
- new paid APIs

These remain later modules until a documented core failure proves they are
needed.

## Coverage Requirements

Every production screener run must report:

- input universe count
- valid ticker count
- market-data coverage count
- enough-history count
- feature-complete count
- eligible count
- ranked count
- rejection counts by reason
- latest data-date distribution

A visually plausible Top 10 is not valid if coverage is unclear.

## Candidate Types

The stable v2 candidate types are:

1. `Stable Leader`
2. `Early Breakout`
3. `Extended Runner`
4. `Rebound Case`
5. `Reject`

Norwegian display labels may be defined separately. Legacy practical-first
labels are not the core v2 domain model.

## Success Criteria

v2 is successful when:

- one clean production screener flow exists,
- universe and coverage quality are explicit on every run,
- candidate ranking is explainable and reviewable,
- baseline and ML challenger can be compared on identical inputs,
- Holdings behavior remains protected,
- the user has rational confidence in why names are surfaced or rejected.

## Non-Goals

The following do not qualify as v2 success by themselves:

- a prettier UI,
- green unit tests alone,
- a good single-date Top 10,
- raw ML rank quality alone,
- more features without measurable candidate improvement,
- agreement with hand-picked favorite tickers only.
