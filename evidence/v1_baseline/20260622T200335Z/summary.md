# TradeTool v2 Phase 1 Baseline Freeze

## Current reproducible v1 state
- Current branch for this audit: `v2/specification` at `f73c68a` (docs only).
- Current approved ML runtime reference: `origin/ML_SCREENER` at `f35517e`.
- Parked practical-first historical reference: `wip/ml-practical-first-a07a27a` at `a07a27a`.
- Old Momentum baseline reference: `main` at `68dbc8301eadf3ac9fe17ef0588ac02405ac0af6`.

## Data date and universe used
- OSE comparison universe: `NORWAY_V2` / benchmark `^OSEAX` / aligned effective date `2026-06-19`.
- S&P 500 comparison universe: `SP500` / benchmark `^GSPC` / aligned effective date `2026-06-09`.

## Baselines captured
- `main_momentum`: old Momentum screener reproduced from `main` on the copied database and aligned dates.
- `current_ml_runtime`: approved `origin/ML_SCREENER` live practical ML runtime behavior.
- `raw_ml`: raw ML score/rank from the active approved model artifact.
- `parked_practical_first`: parked practical-first package for historical reference only.

## Active ML artifacts and evidence
- Active OSE production artifact: `ml_runs/OSE_LGBM_RS_SMA_V2_FIX`.
- Reproducible OSE challenger artifact: `ml_runs/OSE_LGBM_RS_SMA_V2_FULL225_REPRO_20260605`.
- Additional OSE FULL293 reproducible candidate and current SP500 incumbent/challenger were inventoried.
- Existing OSE validation evidence inspected here still ends in 2025; no artifact is described as 2026 validation.

## OSE and S&P 500 coverage
- OSE current ML snapshot: universe=293, scoreable=281, execution-pass=89.
- S&P 500 current ML snapshot: universe=503, scoreable=502, execution-pass=151.
- Per-ticker date coverage and missing-history diagnostics were frozen into `data_date_coverage.csv` and `universe_coverage.json`.

## Old Momentum vs current approved ML vs raw ML vs parked practical-first
- Method-specific Top 20 exports for OSE and S&P 500 were written without forcing a winner.
- Parked practical-first remains historical-only and not production-approved.

## Holdings baseline
- Active positions captured read-only: 7.
- Holdings signal counts: {'HOLD': 6, 'SELL': 1}.
- Holdings UI/report/email path currently shares `analytics.holdings_signals` as the authoritative signal derivation path.

## Phase 1 status
- Phase 1 is complete for baseline freezing, with limitations explicitly recorded for later phases rather than worked around.
