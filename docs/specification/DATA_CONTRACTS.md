# Data Contracts

This document defines the minimum v2 contracts. Dates must be point-in-time
explicit and comparable across modules.

## 1. Universe Member

- required fields: `universe_id`, `ticker`, `as_of_date`, `membership_status`
- optional fields: `instrument_name`, `exchange`, `sector`, `industry`
- types: strings except `as_of_date` date
- null behavior: `ticker`, `universe_id`, `as_of_date` may not be null
- identifiers: `universe_id + ticker + as_of_date`
- date semantics: membership valid for the stated as-of date
- units/scales: none
- validation rules: ticker normalized, membership status enum
- producer module: `tradetool.universe`
- consumer modules: `tradetool.runtime`, `tradetool.diagnostics`

## 2. Price-History Row

- required fields: `ticker`, `date`, `open`, `high`, `low`, `close`, `volume`
- optional fields: adjusted close, source metadata
- types: ticker string, date, numeric OHLCV
- null behavior: OHLC must be non-null for valid rows; volume may be zero but not null
- identifiers: `ticker + date`
- date semantics: market date in the instrument market calendar
- units/scales: price in trading currency, volume in shares or units
- validation rules: `low <= open/high/close <= high`, no duplicate ticker-date rows
- producer module: `tradetool.data`
- consumer modules: `tradetool.features`, `tradetool.diagnostics`

## 3. Benchmark-History Row

- required fields: `benchmark_id`, `date`, `close`
- optional fields: open, high, low, volume
- types: strings, date, numeric
- null behavior: `close` may not be null
- identifiers: `benchmark_id + date`
- date semantics: benchmark market date aligned by explicit join rules
- units/scales: index level
- validation rules: one row per benchmark-date
- producer module: `tradetool.data`
- consumer modules: `tradetool.features`, `tradetool.ranking`

## 4. Feature Row

- required fields: `ticker`, `feature_date`, `coverage_status`, required feature set for the selected engine
- optional fields: sector, benchmark context, artifact compatibility markers
- types: ticker string, date, numeric features, enums
- null behavior: missing required feature means feature-incomplete, not silently eligible
- identifiers: `ticker + feature_date`
- date semantics: all values must be point-in-time valid as of `feature_date`
- units/scales: returns as decimal fractions, volatility and drawdown as decimal fractions, prices in currency
- validation rules: contract versioned, feature completeness explicit
- producer module: `tradetool.features`
- consumer modules: `tradetool.runtime`, `tradetool.ranking`, `tradetool.diagnostics`

## 5. Eligibility Result

- required fields: `ticker`, `feature_date`, `eligible`, `rejection_reasons`
- optional fields: `liquidity_status`, `coverage_notes`, threshold snapshot
- types: ticker string, date, bool, list of strings
- null behavior: empty reasons only when eligible is true
- identifiers: `ticker + feature_date`
- date semantics: eligibility for one production run date
- units/scales: none
- validation rules: deterministic reasons, no silent fallback
- producer module: `tradetool.policy`
- consumer modules: `tradetool.runtime`, `tradetool.explanation`

## 6. Ranked Candidate

- required fields: `ticker`, `rank_date`, `ranking_engine_id`, `raw_rank`, `raw_score`
- optional fields: baseline rank, challenger rank, practical score
- types: string, date, string, integer, numeric
- null behavior: score and rank required for ranked rows
- identifiers: `ranking_engine_id + ticker + rank_date`
- date semantics: rank valid for one run date
- units/scales: raw score engine-defined but documented; ranks one-based integers
- validation rules: one row per ticker per ranking engine and date
- producer module: `tradetool.ranking`
- consumer modules: `tradetool.policy`, `tradetool.diagnostics`, `tradetool.ui`

## 7. Candidate Classification and Trade Signal

- required fields: `ticker`, `rank_date`, `candidate_type`, `trade_signal`, `classification_reasons`
- optional fields: practical score, stretch state, risk notes
- types: strings, date, enums, list of strings
- null behavior: reasons required for non-clean outcomes and recommended for all outcomes
- identifiers: `ticker + rank_date`
- date semantics: classification for one run date
- units/scales: none
- validation rules: candidate type independent from Streamlit, trade signal separated from raw rank
- producer module: `tradetool.policy`
- consumer modules: `tradetool.ui`, `tradetool.holdings`, `tradetool.diagnostics`

## 8. Explanation or Rejection Result

- required fields: `ticker`, `rank_date`, `summary_text`, `reason_codes`
- optional fields: risk bullets, benchmark context, warning flags
- types: strings, date, list of strings
- null behavior: summary text may not be null for displayed rows
- identifiers: `ticker + rank_date`
- date semantics: explanation tied to one run date
- units/scales: none
- validation rules: deterministic from policy state, no ad hoc UI-only prose
- producer module: `tradetool.explanation`
- consumer modules: `tradetool.ui`, `tradetool.diagnostics`, `tradetool.reports`

## 9. Holdings Position

- required fields: `ticker`, `quantity`, `position_status`, `cost_basis_status`
- optional fields: average cost, market value, unrealized P/L, benchmark id
- types: strings, numeric, enums
- null behavior: average cost may be null when cost basis is unknown
- identifiers: `ticker` plus account context where needed
- date semantics: position snapshot date explicit in producer payload
- units/scales: price in currency, quantity in shares or units
- validation rules: unknown cost basis must remain explicit
- producer module: `tradetool.holdings`
- consumer modules: `tradetool.ui`, `tradetool.reports`

## 10. Holdings Signal Result

- required fields: `ticker`, `signal_date`, `holdings_signal`, `signal_reasons`
- optional fields: warning flags, benchmark-relative notes
- types: string, date, enum, list of strings
- null behavior: reasons required
- identifiers: `ticker + signal_date`
- date semantics: signal valid for one holdings evaluation date
- units/scales: none
- validation rules: shared authoritative derivation across UI and reports
- producer module: `tradetool.holdings`
- consumer modules: `tradetool.ui`, `tradetool.reports`

## 11. ML Artifact Metadata

- required fields: `artifact_id`, `artifact_path`, `checksum_set`, `training_start_date`, `training_end_date`, `validation_definition`, `feature_contract_version`
- optional fields: benchmark, universe snapshot id, model family, promotion state
- types: strings, dates, structured checksum map
- null behavior: required fields may not be null
- identifiers: `artifact_id`
- date semantics: training and validation periods must be unambiguous
- units/scales: none
- validation rules: artifact identity reproducible, validation coverage explicitly separate from live coverage
- producer module: `tradetool.artifacts`
- consumer modules: `tradetool.ranking`, `tradetool.diagnostics`

## 12. Coverage Report

- required fields: `run_id`, `run_date`, `input_universe_count`, `valid_ticker_count`, `market_data_coverage_count`, `enough_history_count`, `feature_complete_count`, `eligible_count`, `ranked_count`, `rejection_counts_by_reason`, `latest_data_date_distribution`
- optional fields: benchmark coverage notes, missing ticker samples
- types: strings, date, integers, maps
- null behavior: counts may not be null
- identifiers: `run_id`
- date semantics: describes one specific run
- units/scales: counts as integers
- validation rules: all counts internally consistent
- producer module: `tradetool.diagnostics`
- consumer modules: `tradetool.ui`, `tradetool.reports`, decision gates

## 13. Baseline or Challenger Comparison Result

- required fields: `comparison_id`, `baseline_id`, `challenger_id`, `universe_id`, `run_date`, `cost_assumption_id`, `coverage_summary`, `metric_summary`, `recommendation`
- optional fields: focus-ticker table, notes on limitations, rollback target
- types: strings, date, structured maps
- null behavior: recommendation may not be null
- identifiers: `comparison_id`
- date semantics: one comparison on explicit aligned dates and inputs
- units/scales: returns and drawdowns as decimal fractions, costs documented
- validation rules: identical-input comparison required, limitations explicit
- producer module: `tradetool.diagnostics` or research comparison layer
- consumer modules: approval workflow, artifact promotion
