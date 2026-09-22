# Holdings H4b Real-Data Audit

## Scope

Read-only audit on 2026-09-22. V1 `portfolio.sqlite` and V2
`.local/tradetool_v2.sqlite` were opened with SQLite `mode=ro`. No data was
fetched, refreshed, imported, or written.

## H2b Inputs and Current Sources

`HoldingSignalInputs` requires four booleans: `below_fast_sma`, `weak_rs`,
`below_cost_basis`, and `drop_from_peak`. It also accepts `below_long_sma`,
`short_term_return`, `momentum_acceleration`, and
`fast_sma_slope_positive` for frozen confirmation behavior.

| H2b input / supporting value | Current V2 source | Classification | Required history |
| --- | --- | --- | --- |
| Current close | `price_history_v2.adjusted_close` via `load_price_history_v2_for_tickers` | A | 1 row |
| SMA200 / `below_long_sma` | `compute_raw_features`: `sma200`, `above_sma200` | B | 200 rows |
| Fast SMA / `below_fast_sma` | `compute_raw_features`: `sma50`, `sma100`; no configurable public calculation | B for 50/100; C otherwise | configured SMA rows |
| Fast-SMA slope | no reusable V2 calculation | C | fast-SMA window plus the V1 20-SMA-point comparison |
| 1m return / `short_term_return` | `compute_raw_features`: `return_1m` | B | 21 rows |
| Momentum acceleration | no V2 equivalent; V1 defines it as 1m return minus one third of 3m return | C | 63 rows |
| Cost-basis stop / `below_cost_basis` | H2a `PositionState.gav` and `cost_basis_status`; compare current close to known GAV at the frozen 10% stop | B | current close; known or partially known basis |
| RS / `weak_rs` | V2 has `relative_strength_{1,3,6,12}m`, but these are excess-return differences; V1/H2 threshold `1.05` requires the ratio `asset_gross_return / benchmark_gross_return` | C, semantically incompatible existing feature | $21 \times rs\_months + 5$ rows for stock and benchmark |
| Peak / ATR trailing-stop / `drop_from_peak` | V2 stores required raw OHLC and transactions provide entry context, but no reusable ATR14, post-entry peak, activation, or trailing-stop calculation exists | C | 14 complete OHLC rows after entry; post-entry close history |

`compute_raw_features` is a reusable market-data calculation and uses
adjusted close. Its `drawdown_252` is a window minimum drawdown, not the V1
post-entry peak/ATR trailing-stop calculation, so it is not a substitute.

## Real Held-Ticker Coverage

The read-only V1 aggregation found 7 active instrument groups, all with a
non-empty ticker. All 7 tickers are present in V2 `price_history_v2` under the
`yahoo` source.

- 7/7 have at least 200 rows for SMA200.
- 7/7 have at least 50 and 100 rows for the currently observed fast-SMA values.
- 7/7 have at least 21 rows for 1m return.
- 7/7 have at least 14 rows with complete raw OHLC for ATR.
- No mapped ticker is missing, stale by more than five calendar days, or has
  insufficient history in those categories.

## Benchmark Coverage

`OSEBX.OL` is present in V2 with 1,182 `yahoo` rows from 2022-01-03 through
2026-09-18. All 7 held tickers share at least 131 dates with it, meeting the
maximum six-month RS requirement of 131 rows. The benchmark and all mapped
held tickers have the same latest date (2026-09-18); no benchmark lag warning
applies.

The normal V2 benchmark remains `OSEBX.OL`. V1's `^OSEAX` is not needed for
normal runtime and was not used for this coverage result.

## Remaining V1 Dependency

V1 is only needed as an explicit, one-time read-only source for historical
transactions and settings through the H3 adapters. A normal V2 runtime can use
V2 transactions/settings plus V2 market data; it must not import V1 modules or
query V1. The current blocker is calculation availability, not market-data
coverage or a V1 runtime dependency.

## Calculation Ownership

Keep H2a/H2b pure. A neutral reusable market-data/analytics layer should own
parameterized SMA and slope, 1m/3m return and acceleration, V1-compatible
ratio RS, ATR14, and post-entry peak/trailing-stop primitives. A future
Holdings market-data adapter should load V2 price history and typed settings,
combine these with H2a position and transaction context, and emit only
`HoldingSignalInputs` for H2b. Neither Streamlit nor Screener policy should
own or duplicate these formulas.

## Decision

**BLOCKED_BY_MISSING_SHARED_CALCULATION**

The smallest concrete blocker is the absence of a neutral, reusable
V1-compatible ratio-RS calculation: existing V2 excess-return RS cannot be
compared to the frozen `1.05` threshold. The same shared calculation slice
should also expose the currently absent configurable-SMA/slope and
ATR/post-entry trailing-stop primitives needed to form all H2b inputs.

## Next Smallest Slice

Add and unit-test a pure V2 neutral technical-calculation module for those
inputs, without database access, Holdings persistence changes, UI, or H2b
changes. A subsequent explicit Holdings market-data adapter can consume it.