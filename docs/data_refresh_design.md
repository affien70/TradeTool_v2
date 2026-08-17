# Market Data Refresh Design

## Purpose

This document defines the safe v2 market-data storage and refresh design after
the Phase 4b and Phase 4c diagnostics.

This phase is design-only. It does not fetch market data, write to any
database, migrate data, repair data, or change runtime screener behavior.

## 1. Current Problem

Phase 4b identified:

- `712` invalid OHLC rows
- across `212` tickers
- `66` invalid rows inside the latest 252-row screener window

The dominant invalid patterns were:

- `close_below_low`
- `close_above_high`

Phase 4c concluded the copied legacy database is most likely mixing adjusted
close with otherwise unadjusted OHLC values. The strongest evidence is:

- most violations are close-only boundary violations rather than inverted raw
  OHLC bars
- the issue is spread across many tickers and dates, not isolated to a few bad
  rows
- invalid rows remain present inside the latest 252-row window, so the issue is
  not safely historical-only

Therefore the copied legacy database is unsafe for refresh testing without an
explicit v2 storage contract and an explicit repair-or-rebuild path.

## 2. Target Price Storage Contract

Future v2 market-data storage must separate raw OHLC from adjusted close.

Recommended logical contract for one v2 price row:

- `ticker`
- `price_date`
- `raw_open`
- `raw_high`
- `raw_low`
- `raw_close`
- `adjusted_close`
- `volume`
- `data_source`
- `updated_at`

Recommended semantics:

- raw OHLC represents one internally consistent unadjusted bar
- adjusted close is stored separately and must never overwrite raw close
- `ticker + price_date` is the idempotent natural key
- source metadata must make the upstream provider and extraction mode explicit

Recommended feature usage:

- use `adjusted_close` for returns
- use `adjusted_close` for SMA
- use `adjusted_close` for drawdown
- use `adjusted_close` for volatility
- use `adjusted_close` for relative strength
- use `adjusted_close` for ranking features
- use raw OHLC only for raw OHLC validation
- use raw OHLC later for any candlestick, ATR, or range-based logic

This preserves the current diagnostic screener direction while preventing mixed
price semantics in future refresh flows.

## 3. Refresh Safety Rules

Any future v2 refresh implementation must follow these rules:

- never refresh the original legacy DB
- only refresh an explicit copied or test v2 DB path
- require backup before any write attempt
- require dry-run before any real write
- require schema validation before write
- require one explicit transaction around writes
- require idempotent upsert policy on `ticker + price_date`
- require benchmark refresh with universe refresh
- require post-refresh coverage audit
- require post-refresh readiness audit
- disallow automatic UI-triggered writes

Additional safety requirements:

- fail closed if raw OHLC columns are missing
- fail closed if adjusted close is missing where adjusted-close-based features
  are expected
- reject mixed semantics where raw close and adjusted close cannot be
  distinguished
- produce append-only diagnostics after every dry-run or write run

## 4. Migration and Repair Options

### Option A

Leave the legacy table read-only and introduce a new v2 price table later.

Pros:

- lowest risk to current diagnostics
- no dependence on ambiguous legacy close semantics
- easiest rollback story

Cons:

- requires a new loader and refresh writer
- requires explicit read-path selection once implemented

### Option B

Repair a copied legacy DB by splitting adjusted close from raw close where
recoverable.

Pros:

- could preserve some historical rows without full rebuild
- could support side-by-side comparison with legacy structure

Cons:

- recovery may be incomplete or unverifiable
- historical truth may be unrecoverable from the mixed table alone
- high risk of silent approximation

### Option C

Rebuild a fresh v2 price table from source.

Pros:

- cleanest long-term semantics
- explicit raw/adjusted separation from the start
- best basis for deterministic refresh behavior

Cons:

- requires a new controlled refresh pipeline
- requires careful source and benchmark handling

### Recommendation

Recommended path: combine Option A and Option C.

Concretely:

- keep the legacy table read-only for diagnostics only
- create a new v2 price table with explicit raw/adjusted separation
- populate that new table only through a dry-run-first refresh pipeline against
  temporary test DBs

Option B should not be the default path because it depends on reconstructing
mixed historical semantics from a table already diagnosed as ambiguous.

## 5. Next Implementation Plan

Recommended next phase:

`Phase 4e: create empty v2 market-data schema and dry-run refresh writer against a temporary test DB only`

That next phase should do only the following:

1. define the v2 market-data schema in code and documentation
2. create the empty schema only in a temporary test DB
3. implement a dry-run writer plan with no production writes
4. validate raw OHLC and adjusted close separately
5. run post-write coverage/readiness diagnostics against the temporary DB

That next phase should still not:

- touch the original legacy DB
- promote the new data path into runtime screener reads
- replace current diagnostic read-only behavior

## 6. Implications for Current Runtime

Current read-only diagnostic screener behavior should remain unchanged during
the design and early implementation phases.

Until a clean v2 price store exists:

- current diagnostics may continue reading the legacy copied structure
- refresh testing must not use the mixed legacy table as the target write store
- any future feature or ranking validation must explicitly state whether it was
  run on legacy mixed-close data or on a clean v2 price store

## 7. Non-goals

This phase does not:

- fetch market data
- write to any DB
- migrate data
- repair data
- change ranking
- change features
- change trade policy
- change candidate types
- change ML
- change Holdings
- change Streamlit screener behavior

