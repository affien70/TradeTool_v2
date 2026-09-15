# UI Parity Recovery Gate

## Executive decision

TradeTool v2 must not continue as a full app/UI rewrite. The approved direction is:

`Good V1 user experience + controlled V2 screener/ranking core`

No further screener UI/product work should proceed until this gate is accepted. The next implementation step is exactly:

`build_v1_like_screener_adapter_for_v2_output`

## Evidence inspected

- Legacy repository: `/Users/affien/DEV/TradeTool`
- Legacy branch inspected: `v2/specification`
- Legacy UI files inspected:
  - `main.py`
  - `screener_view.py`
  - `holdings_view.py`
- v2 UI files inspected:
  - `pages/screener.py`
  - `pages/beholdning.py`
  - `src/tradetool/ui/screener.py`
- Existing v2 specification inspected:
  - `docs/specification/HOLDINGS_POLICY.md`
  - `docs/specification/MIGRATION_PLAN.md`
- No screenshot/image files were found in the legacy repo search; evidence is from source and git history.

## V1 UI behavior to preserve or restore

### Screener page flow

The V1 screener flow in `screener_view.py` is built around one familiar workflow:

1. Show `Aksje-screener`.
2. Choose universe in the main control area.
3. Run screener.
4. Show a concise ranked candidates table.
5. Select a row in the table.
6. Show the selected stock's charts and details below.
7. Keep lower-level diagnostics out of the main path.

This general flow should be preserved.

### Screener table behavior

V1 uses an interactive `st.dataframe` with:

- single-row selection
- stable selected ticker stored in session state
- compact overview columns rather than raw engine dumps
- Norwegian user-facing labels
- visual table styling for signal/status where useful

V2 should feed this table from the incumbent screener output, but the table should feel like the V1 table.

### Stock selection flow

V1 selection behavior:

- clicking a row updates the selected ticker
- if no row is selected, the first visible ticker is selected
- the selected ticker drives the detail and chart area

V2 should keep this exact interaction model.

### Detail panel

V1 detail flow combines:

- selected ticker
- company/name metadata where available
- score/signal or ranking context
- key metrics and explanations
- diagnostic details in expanders

V2 should adapt this to incumbent output:

- incumbent rank
- relative strength 6m and 3m
- returns 6m and 3m
- close and latest feature date
- risk level
- risk tags
- Norwegian explanation text

### Price chart

V1 uses a main price chart for the selected ticker, including moving-average context.

V2 should keep a primary selected-ticker price chart using `price_history_v2` and `adjusted_close` as feature close. Chart rendering may use v2-native charting, but layout and behavior should remain familiar.

### Relative-strength / benchmark chart

V1 includes normalized benchmark comparison and relative-strength context.

V2 should continue to show benchmark-relative context for the selected ticker:

- selected ticker indexed price
- benchmark indexed price
- relative-strength line
- benchmark auto-selected from universe

### Key metrics and explanations

V1 does not put raw JSON as the main user interface. It presents metrics and explanation text in readable rows/sections.

V2 should avoid raw JSON in the main screen. Technical payloads belong in a closed expander only.

## What V2 should provide behind the UI

The UI should consume a stable adapter output, not raw internals.

Required adapter contract for the next implementation step:

- baseline ID: `incumbent_naive_rs_6m_top_10_v0`
- universe ID
- universe source
- benchmark ticker
- as-of date
- effective feature date
- close input source: `adjusted_close`
- selected top candidates, ordered exactly as incumbent core ranks them
- full eligible universe for diagnostics
- rejections/data gaps
- per-candidate fields:
  - ticker
  - incumbent rank
  - relative_strength_6m
  - relative_strength_3m
  - return_6m
  - return_3m
  - close
  - latest feature/price date
  - SMA200 status if available
  - liquidity if available
  - drawdown if available
  - risk_level
  - risk_tags
  - Norwegian explanation text

Later, after a separate approved phase, the same adapter may add:

- candidate type
- trade signal
- reject/blocker reasons
- benchmark and universe metadata

## Current V2 UI gaps

### Must restore

- V1-like adapter boundary between screener engine output and UI display.
- Familiar table/detail/chart flow as the primary product direction.
- Single selected-ticker state driving detail and charts.
- Clean Norwegian display labels.
- Main table with user-level fields, not implementation-level fields.
- Details presented as readable metrics/explanations, not raw JSON.
- Data gaps/rejections and engine metadata in expanders.
- Explicit tests that table order is unchanged from incumbent core output.

### Can improve later

- Styling parity with V1 row coloring.
- Company metadata enrichment.
- Period selector and chart period controls.
- Optional normalized benchmark toggle if not always shown.
- More polished chart tooltips or layout.

### Diagnostic-only / remove from main UI

- Raw engine JSON.
- Full eligible universe table on the main path.
- Rejection details on the main path.
- Any legacy diagnostic screener mode as a product-facing feature.
- Any UI surface implying risk tags are filters.

## What must not be touched during UI recovery

- Holdings / Beholdninger page.
- Production DB data.
- Ranking rule.
- Risk-tag rule.
- ML logic or artifacts.
- Holdings logic.
- Market refresh/write controls.
- Candidate type or trade-signal policy unless explicitly scoped later.

## Current unpushed commits

### `8c76fe4 Add single app database configuration`

Recommendation: **partially reuse**.

Keep the useful direction:

- single configured local app DB
- environment override via `TRADETOOL_V2_DB_PATH`
- no DB path input in the screener front page
- Settings/Innstillinger database status
- `.local/` and SQLite/report ignore rules

Review before production UI acceptance:

- DB-path guardrails were broadened to allow repo-local `.local/`
- the app DB creation/population remains manual/test-only
- no market refresh UI should be inferred from this

### `89b1647 Restore familiar screener UI flow`

Recommendation: **do not treat as final product target**.

It is a useful interim correction because it moved away from raw JSON and restored table/detail/chart flow, but it should be superseded by the next step:

`build_v1_like_screener_adapter_for_v2_output`

## Acceptance gate

Before additional screener UI work:

- This recovery plan is reviewed and accepted.
- The next task is limited to the adapter and V1-like screener display.
- No ranking, model, risk-tag, ML, Holdings, or market-refresh changes are bundled with that task.
