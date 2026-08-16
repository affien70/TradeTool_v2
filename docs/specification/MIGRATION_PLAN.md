# Migration Plan

## Principle

v2 proceeds in controlled phases. No phase should hide a winner decision or a
bundled redesign.

## Phase 0

- approve complete `docs/v2`
- no production code

## Phase 1

- freeze and capture reproducible v1 baselines
- old Momentum
- current ML
- focus tickers
- expert or manual sanity sets
- universe and data coverage
- current artifact identities

## Phase 2

- build minimum v2 shell and shared contracts
- reuse proven components
- no feature expansion

## Phase 3

- implement clean baseline screener
- prove universe, data, eligibility, and explanation correctness

## Phase 4

- implement or retrain ML challenger
- updated data and full-enough coverage
- leakage-safe validation

## Phase 5

- compare baseline and challenger
- identical universes, dates, costs, and metrics
- promote only with clear evidence

## Phase 6

- integrate Holdings unchanged or through compatibility layer

## Phase 7

- minimal production UI and controlled local pilot

## Phase 8

- add one approved later module at a time

## Stop Conditions

Stop if:

- the comparison cannot be reproduced on identical inputs
- coverage is unclear
- Holdings parity fails
- a phase tries to bundle unrelated redesign
- a winner is being declared before the gates are satisfied
