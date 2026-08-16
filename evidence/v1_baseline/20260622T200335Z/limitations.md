# Limitations

- No winner is declared here; this is a freeze-and-characterize baseline only.
- Old `main` Momentum was reproduced on the same local universes and DB snapshot, truncated to the ML effective score dates (2026-06-19 for OSE, 2026-06-09 for S&P 500) to avoid false direct cross-date comparison.
- Current ML artifact training extends into 2026 for newer challenger artifacts, but existing validation evidence still ends in 2025 for the OSE artifacts inspected here.
- The current approved OSE runtime snapshot used the live local cache state available on 2026-06-22 and resolved to effective score date 2026-06-19; S&P 500 resolved to 2026-06-09.
- Streamlit cache warnings and Arrow CPU-info warnings occurred in headless read-only execution, but report files were still produced and no source or artifact writes were observed.
- S&P 500 current ML export reproduced safely from cached data, but its validation status remained INVALID in the captured summary and should not be treated as production approval.
- Holdings capture was read-only against a copied SQLite database; no scheduled report, email send, transaction write, or market-data refresh was run.
