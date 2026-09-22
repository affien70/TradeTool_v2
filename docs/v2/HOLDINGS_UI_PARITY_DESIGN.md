# Holdings UI Parity Design

## Scope

H5a is a read-only design audit. It defines a V2 Beholdning presentation layer
over V2 Holdings contracts; it does not implement a page, persistence, import,
or market-data refresh.

## 1. V1 Holdings UI Inventory

The actual V1 `render_holdings_tab` provided:

- Nordnet CSV/TXT upload that parsed and immediately imported transactions;
  it also attempted ticker backfill. There was no preview or confirmation step.
- An `Oppdater priser` button and automatic Yahoo/cache fallback. Both could
  write local market data from the Holdings page.
- An active-holdings table: ticker/name, quantity, cost, current value,
  unrealized P/L in NOK and percent, HOLD/FØLG MED/SELL, and last purchase.
  It included a total row and optional benchmark/sell-plan columns.
- Explicit unresolved-ticker warning, price-freshness caption, and analysis
  failure table.
- A sortable/selectable position table. Selection drove a detail caption with
  signal action, reason, and SELL plan where present.
- A Plotly P/L-by-position bar chart and realized-result donut; a separate
  realized-trade dashboard with metrics, monthly charts, realized timeline,
  and result-by-cost-basis chart.
- A selected-position Plotly price/RS chart. It showed price, one chart SMA,
  RS, optional normalized benchmark comparison, buy-date markers, and
  horizontal reference lines for post-entry peak, configured sell SMA, cost
  stop, and active ATR trailing stop. It had a checkbox for individual buy
  prices and a separate optional normalized-comparison checkbox.
- Holdings-specific controls in the global sidebar: period, RS months,
  sell-SMA days, weak-RS toggle, cost-stop toggle, and ATR trailing-stop
  toggle. V1 used `^OSEAX` for Norway.

Observed omissions: V1 had no normal-user holdings export/download control and
no general raw transaction-timeline table. Its report/email controls lived in
the separate Tools area, not the Holdings tab.

## 2. Current V2 Capability Map

| Concern | Existing V2 public contract | UI consequence |
| --- | --- | --- |
| Source transactions | `HoldingTransactionRecord`, `load_holding_transactions` | Source fields, fee, note, broker ID, and Nordnet result are retained. A caller needs an explicitly initialized Holdings store and connection. |
| Position accounting | `reconstruct_position` -> `PositionState` | Provides open quantity, priced quantity, GAV, remaining cost, known/partial/unknown cost basis, and cash flows for one transaction sequence. |
| Identity/dedupe | `transaction_identity`, `fallback_transaction_key` | Engine owned; never displayed as UI logic. |
| Typed settings | `HoldingSettings`, `load_holding_settings`, `save_holding_settings` | Default Norwegian benchmark is `OSEBX.OL`; persistence is explicit and not a page concern. |
| Market signal inputs | `build_holding_signal_inputs_from_v2_price_history` -> `HoldingMarketDataResult` | Read-only adapter returns as-of date, complete H2b inputs, or explicit unavailable reasons. It does not expose a position view, latest price, SMA values/series, peak, or stop level. |
| Signal decision and text | `evaluate_holding_signal` -> `HoldingSignalEvaluation` | Owns HOLD/FØLG MED/SELL plus exact reasons. |
| Explicit V1 import | `dry_run_v1_holdings_import`, `import_v1_holdings`, and settings counterparts | Imports a V1 database through an explicit adapter. It is not a V2 Nordnet-file parser or a normal V1 runtime dependency. |
| Current V2 presentation | `pages/beholdning.py` | Deliberate shell only; no Holdings orchestration. |
| Plotly pattern | `pages/screener.py`, `tradetool.ui.screener` | Selection by `st.dataframe`, page-level formatting only, and Plotly figures from a UI helper are established patterns. |

## 3. V1 to V2 Parity Matrix

| V1 element | Classification | V2 disposition |
| --- | --- | --- |
| Active-holdings table, selection, filtering/sorting | C | Needs an engine-owned collection of instrument identity, reconstructed position, current valuation, signal result, and unavailable state. UI only selects, filters, sorts, and formats it. |
| Quantity, GAV, cost-basis status | C | `PositionState` has the values, but no V2 position-group/view-result builder maps persisted transactions to page rows. |
| Current price, market value, unrealized P/L, portfolio summary | C | H4d uses price history but exposes only signal inputs/as-of date. A view result must own current-price and valuation outputs. |
| HOLD/FØLG MED/SELL and reasons | C | H2b supplies the final decision/reasons and H4d supplies inputs, but per-position orchestration is absent. |
| As-of/freshness and failed analysis state | B | Render `HoldingMarketDataResult.as_of_date` and `unavailable_reasons` once the page result maps them to positions. |
| Selected-position detail | B | Presentation-only once a selected row and its engine result exist. |
| Price/RS chart with date selection | C | A Holdings chart-detail contract must expose precomputed chart series. The page must not calculate SMA, RS, peak, or stop values. |
| Configured sell-SMA, cost-stop, peak, ATR-stop overlays | C | H4d exposes trigger booleans only; numeric levels and time series are not public Holdings outputs. |
| Buy markers/dates | C | Transaction dates exist, but V2 has no engine-owned open-lot marker result. UI must not reproduce V1 lot-consumption logic. |
| Unresolved ticker warning | B | A view result can expose ticker/market-data availability without UI inference. |
| Realized summary, dividend/fee and realized-trade charts | C | Fee, Nordnet result, and cash flows are retained, but V2 has no realized-result aggregation/timeline contract. |
| Raw transaction timeline | D | Not a normal V1 Holdings UI feature. Do not add it for parity; import preview is the appropriate narrowly scoped history display. |
| Direct Yahoo refresh/automatic cache fill | D | V2 market-data refresh belongs in Settings through its explicit flow, not Beholdning. |
| V1 ticker repair/Yahoo resolution | D | Do not put resolution or external fetch behavior in the V2 page. |
| V1 `^OSEAX` benchmark display/selection | D | Normal V2 runtime uses configured `OSEBX.OL`; `^OSEAX` remains parity-test-only. |
| Daily email/report controls | D | Not part of the V1 Holdings tab and not an H5 page responsibility. |

## 4. Ownership Boundary

**Engine owned:** transaction identity/dedupe; grouping and position
reconstruction; quantity/GAV/cost-basis state; latest price, valuation and P/L;
SMA, RS, ATR, peak, stop values; `HoldingSignalInputs`; signal evaluation and
reason text; unavailable classifications; benchmark selection; import parsing,
dry-run, idempotency, and writes.

**UI owned:** requesting an engine result; selecting, sorting, filtering, and
formatting result rows; rendering metrics, tables, status messages, and Plotly
figures from engine-provided series/overlays; gathering explicit confirmation
for future write actions.

## 5. Proposed Beholdning Page Structure

1. **Header and availability**: read-only caption for Holdings-store state,
   configured benchmark (`OSEBX.OL`), and latest relevant market as-of date.
   Empty state: no imported transactions. Unavailable state: show engine
   reason, never attempt a fetch or initialize a schema.
2. **Portfolio summary**: active-position count, total current value, total
   cost where known, and unrealized P/L where valid. Read-only. These are
   fields of the future `HoldingsPageResult`, not page calculations.
3. **Active holdings**: selectable `st.dataframe` with ticker/name, quantity,
   GAV or `unknown`/`partially unknown`, latest price/as-of date, valuation/P&L
   when available, action, and reason/unavailable status. Read-only; page
   sorting/filtering only. Empty state directs the user to the future explicit
   import flow.
4. **Selected position**: instrument header, status/action, engine reasons,
   cost-basis label, and market-data unavailable state. Read-only.
5. **Selected-position chart**: Plotly price chart described below. Read-only;
   render a chart-specific empty/unavailable message when the engine has no
   chart points.
6. **Import and settings entry points**: collapsed, disabled/absent until the
   corresponding explicit engine services exist. They must not run from the
   initial read-only page slice.

Realized-result dashboard is deferred until its engine aggregation contract
exists; it is not needed for the first useful active-position parity slice.

## 6. Plotly Chart Contract

Only Plotly figures may be rendered. No `st.line_chart`, `st.area_chart`,
Vega-Lite, or Altair.

The future engine chart-detail result must provide all numeric series and
overlay levels. The page creates the figure only.

| Chart item | Contract |
| --- | --- |
| X axis | Trading date, bounded by typed `HoldingSettings.period_label`/selected chart period and the requested as-of date. |
| Main series | V2 adjusted-close price for the selected ticker. Tooltip: date, close, and volume where available. |
| SMA overlays | Configured sell SMA when enabled, plus SMA200. Tooltip carries the supplied SMA values. The UI never derives them. |
| Buy markers | Render only engine-provided transaction/open-lot markers with date, known price, and unknown-cost label where applicable. |
| Reference overlays | Render only engine-provided GAV/cost-stop, post-entry peak, and active ATR trailing-stop levels. Do not calculate or infer a missing level. |
| RS/benchmark comparison | Optional secondary Plotly panel only when the engine supplies aligned/indexed series using configured `OSEBX.OL`. Tooltip identifies both series and baseline date. |
| Period handling | UI passes the chosen approved period/as-of date to the engine; the engine returns visible data and a warning/unavailable state. |
| Empty state | Show ticker, requested period, as-of date, and engine-provided unavailable reason; do not fetch/refresh. |

This retains V1's useful selected-position workflow while removing V1's
Streamlit-owned analytics and Yahoo/cache writes.

## 7. Import UX Contract

V1 immediately imported an uploaded Nordnet file. V2 must not restore that
write-on-upload behavior.

The minimum later V2 workflow is:

1. Select a Nordnet file in a dedicated import surface.
2. Invoke an engine-owned parse/dry-run API and render source-row, valid-row,
   invalid-row, duplicate, would-insert, and would-update counts.
3. Render a bounded preview of normalized transactions and any validation
   errors, without internal database identifiers.
4. Require an explicit confirmation checkbox/button before an engine-owned
   write API runs.
5. Render the persisted import receipt and refresh the read-only Holdings page
   result.

Current V2 provides dry-run/idempotency feedback only for the explicit V1
database import adapter. It provides no Nordnet-file parser or production
write service; no file-upload UI is authorized until that engine contract is
approved. V1 remains read-only and is not a normal runtime source.

## 8. Settings UX Contract

When an explicit settings service exists, keep V1-visible controls together in
a compact Beholdning signal-settings expander:

- `period_label`: visible/editable for chart and analysis period.
- `rs_months`: visible/editable.
- `sell_fast_sma_days`: visible/editable, including `0` to disable.
- `sell_rs_weak`, `sell_below_cost_basis`, `sell_drop_from_peak`: visible
  editable toggles.

Keep `atr_multiplier=2.5` and `rs_threshold=1.05` as advanced/default
configuration, not ordinary page controls; V1 did not serialize them in its
Holdings UI. Show `norway_benchmark_id=OSEBX.OL` as read-only context if useful;
do not offer `^OSEAX` as the normal V2 choice. Settings writes require an
explicit engine/service API and a confirmation path, not direct Streamlit SQL.

## 9. Missing Engine/API Gaps

The smallest blocking gap is a read-only, engine-owned **Holdings page-result
adapter**. Given an initialized V2 Holdings store, typed settings, and V2
market database, it must return:

- active positions grouped by engine-owned instrument identity;
- each `PositionState`, typed transaction/display metadata, latest price/as-of
  date, valuation/P&L availability, `HoldingSignalEvaluation`, and H4d
  unavailable reasons;
- portfolio summary fields with explicit partial/unknown cost-basis treatment;
- a selected-position chart-detail containing precomputed price/SMA/benchmark
  series and only available overlay levels; and
- safe missing-store, missing-ticker, and insufficient-history states.

It must read V2 only, call the existing H2a/H2b/H4d contracts, contain no
Streamlit, make no network calls, and write nothing. This adapter is required
before the page can remain a pure presentation layer.

Separate later gaps are the explicit V2 Nordnet parse/dry-run/write service and
realized-result aggregation. Neither belongs in the initial page shell.

## 10. H5 Implementation Slices

1. **H5b: read-only Holdings page-result adapter.** Add the missing engine
   contract and focused tests for position grouping, known/partial/unknown
   basis, signal/unavailable propagation, and no-write behavior.
2. **H5c: read-only page shell, summary, and selection table.** Replace only
   the V2 shell; render H5b output using established table conventions. Keep
   the user-maintained page title unchanged.
3. **H5d: selected-position Plotly chart detail.** Add the engine chart result
   and render price/SMA/benchmark/marker/available-stop overlays without page
   calculations.
4. **H5e: explicit import and settings services/UI.** First approve V2
   Nordnet parse/dry-run/write and settings-write contracts, then add preview,
   confirmation, and idempotency feedback.
5. **H5f: realized-result parity decision and runtime validation.** Add
   aggregation only if approved; validate empty, unknown-cost, unavailable
   market data, imported transactions, and real V2 market-data paths.

## 11. H5 Completion Criteria

- Beholdning consumes V2-only engine results and has no V1 import/query,
  market-data fetch, schema initialization, or business calculations.
- Active positions show quantity, GAV/status, current valuation availability,
  H2b action, exact engine reasons, and H4d unavailable states.
- The selected-position Plotly chart follows the contract above and never
  computes SMA, RS, ATR, peak, or stops in Streamlit.
- V2 uses `OSEBX.OL` for normal Norwegian Holdings runtime.
- Import writes follow dry-run plus explicit confirmation and report
  idempotency outcomes.
- Existing Screener behavior and the user-maintained Beholdning title remain
  unchanged except for the authorized future page work.

## 12. Explicit Non-Goals

- No copying V1's Streamlit-owned accounting, signal, chart, ticker-repair,
  Yahoo fetch, or cache-write logic.
- No normal V1 database/runtime dependency and no `^OSEAX` runtime migration.
- No holdings database/schema initialization from page reads.
- No direct-write upload, automatic refresh, report/email feature, generic raw
  transaction timeline, ranking change, or Screener change in H5b-H5d.

Single smallest blocker: the read-only engine-owned Holdings page-result adapter
defined in section 9.

BLOCKED_BY_MISSING_ENGINE_API