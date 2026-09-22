# Holdings Storage Design

## 1. Current V2 State

The configured V2 app database is `.local/tradetool_v2.sqlite`, with an optional
`TRADETOOL_V2_DB_PATH` override. Its current schema contains only:

- `price_history_v2`, keyed by `(ticker, price_date, data_source)` for validated
  market-data rows.
- `universe_cache`, a local cache separate from the market-data schema module.

`tradetool.data.sqlite_readonly.ReadOnlySQLite` opens SQLite with `mode=ro` and
permits only `SELECT`, `WITH`, and `PRAGMA`. Market-data DDL lives separately in
`tradetool.data.market_data_schema`; it has an explicit initializer and no
general migration framework. `price_history_v2` readers are pure read adapters
and do not participate in Holdings logic.

There is no generic V2 application-settings persistence. `pages/innstillinger.py`
displays database status and controls market-data refresh only.

H2a reconstructs positions from pure transactions. H2b evaluates a pure signal
from explicit rule inputs. Neither currently reads or writes SQLite.

## 2. V1 Storage Facts

V1 uses `portfolio.sqlite` with these relevant tables:

- `holdings`: transaction rows with `id`, `ticker`, `ticker_source`, `fullname`,
  `isin`, `Verdipapir`, `buy_date`, `settlement_date`, `shares`, `buy_price`,
  `note`, `transaksjonstype`, `valuta`, `belop`, `kurtasje`,
  `nordnet_resultat`, `dagens_verdi`, `pnl_nok`, `transaksjons_id`,
  `transaction_key`, and `cost_basis_missing`.
- `app_settings`: `setting_key` primary key, `setting_value`, and `updated_at`.

V1 import prefers the Nordnet transaction ID (`transaksjons_id`), then a
deterministic `transaction_key`. The fallback key derives from trade date,
settlement date, ISIN/ticker/instrument name, transaction type, normalized
shares, and currency; it ignores price and amount. V1 has unique identity
enforcement for both broker ID and fallback key and an ordering index for
position reconstruction.

`kurtasje` and `nordnet_resultat` support fee and realized-result reporting.
Dividend and fee transaction types remain source transactions; H2a preserves
the frozen non-position cash-flow behavior from transaction type and amount.

Verified V1 Holdings settings are:

| Setting | Code default | Verified persisted value |
| --- | --- | --- |
| `period_label` | `1 år` (`1y`) | `5 år` (`5y`) |
| `rs_months` | `6` | `6` |
| `sell_rs_weak` | `true` | `true` |
| `sell_below_cost_basis` | `true` | `true` |
| `sell_drop_from_peak` | `false` | `true` |
| `holdings_sell_sma_days` | `100` | `50` |
| `holdings_atr_multiplier` | `2.5` | no persisted row; code default applies |
| `sell_rs_threshold` | `1.05` | code-only; no persisted row |

No transaction or position values were inspected for this audit.

## 3. Minimum V2 Persistence Requirements

### Transactions

Persist source transactions, not reconstructed positions. H2a needs a stable
transaction type, quantity, price/amount, identity fields, and chronological
ordering inputs to derive quantity, remaining cost, GAV, cost-basis state, and
cash flows deterministically.

Required source fields are broker transaction ID, fallback transaction key,
trade date, settlement date, ISIN/ticker/instrument name, transaction type,
shares, price, amount, currency, and fee. `ticker_source`, source cost-basis
marker, and Nordnet result are retained for import audit/history but are not
H2a inputs.

### Settings

Persist only the Holdings controls consumed by H2b: period label, RS months,
weak-RS toggle, cost-stop toggle, ATR trailing-stop toggle, fast sell SMA days,
ATR multiplier, and RS threshold. Derive `period_key` from `period_label`; do
not persist duplicate period semantics. Apply deterministic code defaults when
the singleton settings row is absent.

### Derived Data Not To Persist

Do not persist current open quantity, priced quantity, remaining cost, GAV,
cost-basis status, HOLD/FØLG MED/SELL, signal reasons, or benchmark-relative
results. H2a/H2b derive these from transactions, explicit settings, and
separately supplied market features. No derived-position or signal table is
needed for H3b.

## 4. Storage Strategy Comparison

| Option | H2a/H2b compatibility | Risk and maintenance | Rollback / V1 dependency |
| --- | --- | --- | --- |
| A. Reuse V1 `holdings` / `app_settings` schemas | High column-level parity, but retains V1 mixed transaction/current-value fields and untyped shared settings. | High semantic-coupling risk; encourages V1 schema and runtime dependence. | Poor rollback isolation; V1 could become a runtime dependency. |
| B. V2-native Holdings tables plus explicit V1 import adapter | Directly maps H2a source inputs and H2b typed settings while preserving selected audit fields. | Low coupling; one small, versioned V2 schema to maintain. | Simple rollback by disabling the adapter or using a fresh V2 DB; V1 remains read-only input only. |
| C. Extend `price_history_v2` or `universe_cache` | Does not fit transaction or settings semantics. | Couples market data/cache to portfolio domain and obscures ownership. | Poor; no V1 benefit and no clean rollback boundary. |

**APPROVED STORAGE STRATEGY:** Option B. V2 uses native Holdings persistence
with an explicit, idempotent V1 import/compatibility adapter. V1 remains
strictly read-only and is never a normal V2 runtime dependency. Source
transactions are persisted; H2a/H2b continue to derive open positions, GAV,
cost-basis status, signal action, and signal reasons. No derived position or
signal table is allowed unless a future measured requirement explicitly
justifies one.

## 5. Proposed H3b Contract

This is a proposed contract only. H3b must create no tables until approved.

### `holdings_transactions_v2`

| Column | Type / constraint | Purpose |
| --- | --- | --- |
| `transaction_id` | `INTEGER PRIMARY KEY` | V2 surrogate key. |
| `nordnet_transaction_id` | `TEXT` nullable | Broker identity when present. |
| `fallback_transaction_key` | `TEXT NOT NULL UNIQUE` | H1 deterministic identity for fallback/idempotency. |
| `trade_date` | `TEXT NOT NULL` | H2a chronological order. |
| `settlement_date` | `TEXT` nullable | H2a chronological order. |
| `isin`, `ticker`, `instrument_name` | `TEXT` nullable | Instrument identity in H1 fallback order. |
| `ticker_source` | `TEXT` nullable | Source audit only. |
| `note` | `TEXT` nullable | Optional imported audit/user information. |
| `transaction_type` | `TEXT NOT NULL` | H2a purchase/sale/transfer/cash-flow classification. |
| `shares` | `REAL NOT NULL` | Source quantity. |
| `price`, `amount`, `fee` | `REAL` nullable | Source accounting values. |
| `currency` | `TEXT` nullable | Fallback identity and source context. |
| `nordnet_result` | `REAL` nullable | Audit/history for later realized-result work. |
| `source_cost_basis_missing` | `INTEGER NOT NULL DEFAULT 0` | Preserve V1 source marker without adding new H2a behavior. |
| `created_at_utc`, `updated_at_utc` | `TEXT NOT NULL` | Import audit. |

Proposed indexes: a partial unique index on non-empty
`nordnet_transaction_id`, and a non-unique order index on
`(isin, ticker, trade_date, settlement_date, fallback_transaction_key)`.

### `holdings_settings_v2`

One typed global row with `settings_scope TEXT PRIMARY KEY` constrained to
`global`, plus `updated_at_utc TEXT NOT NULL`:

- `period_label TEXT NOT NULL`
- `rs_months INTEGER NOT NULL`
- `sell_rs_weak INTEGER NOT NULL`
- `sell_below_cost_basis INTEGER NOT NULL`
- `sell_drop_from_peak INTEGER NOT NULL`
- `sell_fast_sma_days INTEGER NOT NULL`
- `atr_multiplier REAL NOT NULL`
- `rs_threshold REAL NOT NULL`
- `norway_benchmark_id TEXT NOT NULL`

Use explicit checks for allowed period labels, `rs_months` values, booleans,
non-negative SMA days, and positive multiplier/threshold. Missing row fallback
is the documented H1 code-default set. `period_key` is derived from the label.
The missing-row fallback for `norway_benchmark_id` is `OSEBX.OL`.

## 6. V1 Import / Migration Approach

Do not migrate implicitly. A future explicit import command should accept an
approved V1 database path or copy, open it read-only, map transaction rows into
the proposed V2 contract, and write only after dry-run validation and explicit
authorization.

The importer must calculate the H1 fallback key, prefer Nordnet ID, deduplicate
idempotently by either identity, preserve chronological source fields, and be
tested against a temporary V2 database first. Rollback is deletion of the
separate V2 Holdings database state or restoration from a pre-import V2 backup;
V1 is never changed.

V1 source transaction rows map directly to the proposed source and audit
columns. `note` is preserved as optional imported audit/user information.
`dagens_verdi` and `pnl_nok` are not source-of-truth V2 data and must be
recalculated from current V2 data if later needed. Other legacy-only fields not
required for H2a reconstruction are excluded unless a transaction-audit or
traceability requirement explicitly justifies them. No derived V1 position or
signal values should be migrated.

## 7. Benchmark Configuration — Approved

Holdings benchmark selection is configuration, not signal-engine behavior. The
V2 default benchmark for Norway is `OSEBX.OL`, persisted as
`norway_benchmark_id` in the typed Holdings settings contract. H2b continues to
receive benchmark-relative feature inputs explicitly; it does not choose or
load a benchmark.

`OSEBX.OL` aligns with the V2 `NORWAY_V2` market-data update configuration.
It replaces V1 benchmark behavior for normal V2 runtime and therefore changes
RS inputs relative to V1.

`^OSEAX` may remain available only for explicit V1 parity or comparison tests
when required. It is not a normal V2 runtime dependency and is not the default
Norwegian Holdings benchmark.

H3b benchmark tests must verify deterministic `OSEBX.OL` fallback and prove
that alternate benchmark configuration remains outside the signal engine.

## 8. H3b Acceptance Criteria

- Implements the approved Option B schema in an isolated Holdings persistence
  boundary; V1 remains read-only and is never a normal V2 runtime dependency.
- DDL is isolated from market-data schema initialization and covered by
  temporary-database schema tests.
- Import dry-run and write paths are distinct; the write path is explicitly
  authorized and idempotent by both identity mechanisms.
- Import preserves V1 `note`, excludes `dagens_verdi` and `pnl_nok` as
  source-of-truth data, and excludes other legacy-only fields unless required
  for transaction audit or traceability.
- H2a reconstructs the H1 transaction cases solely from V2 transaction rows.
- H2b receives typed settings, explicit benchmark/feature inputs, and the
  deterministic Norway default `OSEBX.OL` without database access or duplicated
  signal policy.
- No derived position or signal rows are persisted.

## 9. Explicit Non-Goals

H3a does not implement Streamlit, database writes, migration execution,
market-data refresh, daily reporting/email, settings UI, or new
signal/accounting behavior. V1 remains a read-only, explicit import source and
never a V2 runtime dependency.