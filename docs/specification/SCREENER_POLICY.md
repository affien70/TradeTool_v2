# Screener Policy

## Policy Goal

The v2 screener policy must rank plausible buy candidates in a way that is
explainable, deterministic, and independent from UI implementation details.

Policy is defined independently of the old practical-first patch language.

## Required Production Policy Chain

```text
data quality
-> tradability/liquidity
-> eligibility filters
-> baseline/challenger ranking
-> practical execution policy
-> candidate classification
-> explanation
```

## Policy Invariants

- model score cannot rescue a failed eligibility candidate
- ownership cannot reduce ranking, Top N inclusion, practical score, or BUY
  eligibility
- candidate classification must be independent from Streamlit
- rejection reasons must be explicit and deterministic
- thresholds must be versioned
- ranking and trade policy must remain separate concepts
- raw score, practical signal, and candidate type must not be conflated

## Candidate Types

The stable v2 candidate types are:

1. `Stable Leader`
2. `Early Breakout`
3. `Extended Runner`
4. `Rebound Case`
5. `Reject`

These are the core domain model. Legacy labels such as `Praktiske kandidater`,
`Sterk, men under ML-terskel`, and `ML-review / høy risiko` may appear only in
migration notes or compatibility mappings.

## Eligibility Foundation

Eligibility decisions are made before ranking can matter.

Minimum production checks:

- universe membership valid
- market data present
- enough history for required features
- feature row complete enough for the chosen ranking engine
- price and benchmark dates consistent enough for the run
- tradability and liquidity acceptable
- explicit rejection reason if a row fails

## Ranking Layer

The ranking layer may be:

- a clean baseline screener
- a controlled ML challenger

Both must operate on identical contracts, identical universes, identical dates,
and identical cost assumptions when compared.

Raw ML ranking must never be the unrestricted user-facing buy list.

## Practical Trade Policy

The practical trade policy is downstream of ranking.

It may:

- suppress or downgrade names with poor tradability
- separate clean candidates from risky cases
- distinguish plausible entries from extended or rebound situations
- attach trade signals and reasons

It must not:

- overwrite the underlying raw rank semantics
- silently pass weak or incomplete data
- depend on UI state

## Candidate Type Intent

### `Stable Leader`

Clean, trend-supported candidates with supportive momentum, RS, tradability,
and risk profile.

### `Early Breakout`

Improving candidates that are not yet as mature as stable leaders but show
credible strengthening behavior without reading as junk rebound names.

### `Extended Runner`

Strong names with credible leadership signals but elevated stretch or entry-
timing risk.

### `Rebound Case`

Model- or ranking-interest cases where practical quality is weaker, reversal-
like, or otherwise risky enough that the name should not be treated as a clean
buy candidate.

### `Reject`

Rows that fail data, eligibility, tradability, or practical quality
requirements.

## Explanation Rules

Every candidate or rejection must have explicit reasons drawn from policy code,
for example:

- insufficient data coverage
- stale data
- under long-term trend threshold
- weak relative strength
- weak medium-term momentum
- low liquidity
- excessive drawdown
- excessive stretch
- benchmark-context failure
- rebound-like risk profile

## Coverage Reporting

Every run must report:

- input universe count
- valid ticker count
- market-data coverage count
- enough-history count
- feature-complete count
- eligible count
- ranked count
- rejection counts by reason
- latest data-date distribution
