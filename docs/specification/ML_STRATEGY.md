# ML Strategy

## Strategic Position

ML must be treated as a serious challenger, not permanently secondary and not
pre-approved as the production winner.

The correct sequence is:

```text
build clean baseline
-> build controlled ML challenger
-> compare on identical data, universes, dates and costs
-> promote only the objectively better robust solution
```

## Current Role

The current ML model is an incumbent or challenger input. It may inform:

- challenger evaluation
- shadow ranking
- explanation support or disagreement
- focus-ticker analysis
- live diagnostic exports

It is not automatically approved as the main ranking engine.

## Required ML Foundations

ML work must define and verify:

- updated training data through the current approved cutoff
- full-enough universe coverage
- strict point-in-time feature construction
- walk-forward or OOF validation
- survivorship and constituent limitations
- realistic turnover and trading costs
- artifact identity and checksums
- shadow evaluation
- explicit promotion and rollback

## Promotion Requirements

ML can be promoted only if it shows clear improvement over the baseline in:

- top-N candidate plausibility
- top-vs-bottom future return spread
- drawdown
- stability across periods
- stability across universes
- turnover and cost behavior
- explanation correctness
- focus-ticker sanity
- reject-reason correctness

ML is not required to agree with manual or expert tickers. It must explain
disagreement rationally.

## Point-In-Time and Coverage Rules

ML validation and reporting must distinguish:

- live coverage
- training coverage
- validation and backtest coverage

Existing artifacts must not be described as 2026 validation unless their
validation evidence explicitly supports that claim.

## Artifact Requirements

Each challenger or incumbent artifact must carry:

- stable run identifier
- metadata checksum references
- feature contract version
- training date range
- validation window definition
- universe definition
- benchmark definition
- selected target label
- promotion state
- rollback target if promoted

## Rollback Rule

If an ML challenger is promoted, the promoted state must still preserve:

- previous approved baseline reference
- prior production artifact identity
- rollback instructions
- comparison evidence that justified promotion
