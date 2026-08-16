# Definition of Done

## Production-Ready Means

TradeTool v2 is production-ready only when all of the following are true:

- one clean production screener flow exists
- reproducible universe and market-data build exists
- current approved data cutoff is explicit
- sufficiently complete OSE and S&P 500 coverage is demonstrated
- versioned data and feature contracts exist
- robust baseline vs challenger comparison exists
- the production model or config is explicitly promoted
- candidate types and trade policy are locked
- explanations and rejection reasons are verified
- sanity-set checks are completed and disagreements are rationally explained
- Holdings parity is passed
- the full regression suite is passed
- rollback path is documented and tested
- runtime does not depend on research scripts
- the user has genuine confidence in the candidate output

## What Does Not Count As Done

The following do not qualify as done:

- green unit tests alone
- a good single-date Top 10
- raw ML rank quality alone
- a prettier UI
- more features without measurable candidate improvement
- passing only hand-picked ticker examples

## Approval Rule

If any done criterion is missing, v2 remains in specification, migration, or
pilot state rather than production-ready state.
