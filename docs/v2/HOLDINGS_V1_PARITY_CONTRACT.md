# Holdings V1 Parity Contract

## Scope

H1 freezes synthetic, deterministic V1 Holdings observations for H2. It does
not implement a V2 Holdings engine, UI, persistence schema, or settings
migration.

Fixture: `tests/fixtures/holdings_v1_parity/holdings_v1_parity.json`

## V1 Evidence

Source modules inspected:

- `holdings_service.py`: Nordnet parsing/import, transaction identity,
  idempotent import, active-position reconstruction, cost basis, and cash-flow
  rows.
- `analytics.py`: `build_holdings_rules_from_settings`,
  `resolve_holdings_benchmark`, `holdings_signals`, and
  `evaluate_holding_sell_signal`.
- `holdings_view.py`: shared signal consumption and Holdings presentation.
- `daily_holdings_report.py`: shared settings/signal consumption and report
  payload construction.

V1 tests used as evidence:

- `tests/test_holdings_service.py`: idempotent overlapping Nordnet imports,
  partial/full sales, active-position cost reduction, and canonical ticker
  import behavior.
- `tests/test_analytics.py`: HOLD, FØLG MED, and SELL confirmation cases for
  SMA100, SMA200, RS, 1m momentum, cost stop, and ATR trailing stop.
- `tests/test_holdings_view.py`: unknown-cost transfer markers and active buy
  lots after transfers/partial sales.
- `tests/test_daily_holdings_report.py`: shared FØLG MED signal/reason across
  GUI-facing data, export, daily report, and email body.

## Frozen Behavior

The fixture freezes:

- Nordnet transaction-ID priority and deterministic fallback identity fields.
- Open quantity, remaining cost basis, GAV, and known/partially unknown/unknown
  cost-basis states for synthetic purchase, sale, transfer, duplicate, dividend,
  and fee cases.
- A complete sale removes the active position.
- Dividend and fee rows do not change the open quantity or cost basis; they are
  reporting cash flows.
- V1 signal action and exact reason text for healthy positions, isolated versus
  confirmed SMA100/ATR breaks, SMA200 break, 10% cost stop, weak RS, and
  negative 1m momentum.
- The V1 shared signal path is authoritative for Holdings UI, export, daily
  report, and email payloads.

## Settings

V1 source establishes the following code defaults. The persisted values below
were verified by a read-only query of only these Holdings-relevant rows in V1's
`portfolio.sqlite` `app_settings` table; no transaction or position rows were
read.

| V1 setting | Holdings signal use | Code default | Persisted value |
| --- | --- | --- | --- |
| `period_label` | History period for Holdings signal analysis; derives `period_key` | `1 år` (`1y`) | `5 år` (`5y`) |
| `rs_months` | RS lookback for Holdings SELL evaluation | `6` | `6` |
| `sell_rs_weak` | Enables SELL confirmation for weak RS | `true` | `true` |
| `sell_below_cost_basis` | Enables the 10% below-cost stop | `true` | `true` |
| `sell_drop_from_peak` | Enables ATR trailing-stop behavior | `false` | `true` |
| `holdings_sell_sma_days` | Fast SMA period; `0` disables this filter | `100` | `50` |
| `holdings_atr_multiplier` | ATR trailing-stop multiplier | `2.5` | No persisted row; effective value is the code default `2.5` |
| `sell_rs_threshold` | Weak-RS threshold used by the signal rules | `1.05` | Not a persisted setting; V1 does not read it from `app_settings` |

The current V1 Holdings UI serializes the first six persisted settings above.
It does not serialize `holdings_atr_multiplier`; the rule builder nevertheless
accepts a stored override if one exists. The fixture's test-backed override
example remains a contract example, not a persisted-value claim.

## Deferred / Open

- **OPEN DECISION:** V1 uses `^OSEAX` for Norwegian Holdings benchmark
  resolution. H1 does not decide whether V2 should use `^OSEAX` or `OSEBX.OL`.
- H1 does not freeze external ticker-resolution provider behavior, real-user
  transaction data, realized-PnL reporting detail, UI layout, database schema,
  or settings persistence/migration.

V1 is reference only and must not become a runtime dependency.