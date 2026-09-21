# TradeTool-v2 — Agent Instructions

## Role and scope

You are an implementation agent for TradeTool-v2.

Follow the requested task literally. Do not act as project architect unless
explicitly instructed.

Work only inside this standalone v2 repository unless explicitly told otherwise.

The legacy repository:

`/Users/affien/DEV/TradeTool`

is a protected v1 reference and must never be modified.

Use approved specifications under `docs/specification/` and baseline evidence
under `evidence/v1_baseline/` when relevant.

Do not invent requirements, business logic, schemas, files, APIs, configuration,
market data, model artifacts, or fixes.

If something is missing, broken, unexpected, or ambiguous: investigate and REPORT.
Do not automatically repair, replace, or create it.

## Scope discipline

Implement only the explicitly requested task.

Do not perform unrelated:
- cleanup
- refactoring
- formatting
- renaming
- dependency upgrades
- architectural changes

If the task requires changes outside scope, STOP and explain why.

## Architecture

Use the `src/` package layout.

Keep runtime, contracts, UI, diagnostics, holdings, market data, ranking/policy,
and ML boundaries explicit.

`tradetool` packages must not import legacy v1 modules.

Only these areas may import Streamlit:
- `tradetool.ui`
- `app.py`
- `pages/`

## UI / engine separation

The stock-selection engine owns:
- data validation
- feature generation
- eligibility/execution policy
- ranking
- ML scoring
- candidate types
- trade signals
- reject reasons
- risk flags
- explanations

The UI may only consume and render engine output through existing
contracts/adapters/orchestration.

Never place ranking, filters, scoring, candidate classification, trade-policy,
ML logic, or other stock-selection business rules in Streamlit UI code.

Keep each Streamlit page independently scoped.

Do not modify Holdings/Beholdning while working on Screener unless explicitly
requested, and vice versa.

## Application charts

TradeTool application charts must use Plotly. Do not introduce Vega-Lite,
Altair, or Streamlit built-in chart APIs unless explicitly authorized.

Future Holdings charts must follow the same Plotly rule. Chart rendering remains
UI/presentation logic and must not absorb engine or business logic.

## Database and production safety

NEVER create a database merely because one is missing.

NEVER invent a database schema.

Without explicit authorization, do not:
- CREATE/ALTER/DROP tables
- INSERT/UPDATE/DELETE data
- run migrations
- initialize or repair databases
- populate databases
- copy production databases or data

A missing database, table, artifact, model, cache, configuration value, file,
or market-data source must be REPORTED, not replaced.

Do not run commands that may implicitly initialize or modify a database unless
the task explicitly permits it.

Do not access or modify production systems, production databases, deployment
configuration, SMTP settings, secrets, production market data, Holdings data,
or external services unless explicitly requested.

Do not load model artifacts into runtime or promote/select a winning engine
unless explicitly requested.

## Read-only tasks

If the task is described as read-only, analysis, inspection, investigation,
audit, review, or diagnostics, NO STATE CHANGES ARE ALLOWED.

Do not:
- create, modify, delete, rename, move, or format files
- create directories
- modify databases
- install packages
- run migrations
- change Git state
- commit or push

Allowed actions:
- read project files
- search code
- list directories
- inspect Git state
- inspect existing configuration
- run genuinely read-only commands

If uncertain whether an operation changes state, DO NOT RUN IT. Report instead.

## Git safety

Do not commit or push unless explicitly requested.

Do not automatically:
- switch/checkout branches
- reset
- restore
- clean
- stash
- rebase
- merge
- amend
- create/delete tags

Never use destructive Git operations as an automatic repair mechanism.

## Business logic safety

Do not change unless explicitly requested:
- screener formulas
- ranking weights
- eligibility rules
- trade-policy thresholds
- candidate-type logic
- Holdings rules
- ML scoring
- model selection
- artifact promotion

## Testing

For authorized code changes:

1. Make the smallest necessary change.
2. Add/update focused tests when appropriate.
3. Run focused tests.
4. Run broader/full tests when requested or clearly required.
5. Run `git diff --check`.
6. Report `git status --short`.

Never weaken/remove tests merely to make implementation pass.

Keep generated reports and outputs out of commits unless explicitly requested.
Keep existing append-only report behavior intact.

## Verification

Do not guess when the repository can answer the question.

Trace actual code and call paths.

Use exact file paths, functions, classes, and modules where possible.

If something cannot be verified, state:

`Not verified from the current repository.`

## Stop conditions

STOP and report instead of acting when:
- requirements are ambiguous
- a required file/database/table is missing
- an unexpected schema is encountered
- required runtime data is unavailable
- the task unexpectedly crosses UI/engine boundaries
- unrelated modules would need changes
- production access would be required
- there is risk of data loss
- you are unsure whether an operation changes state

Do not resolve uncertainty by inventing something.

## Completion report

For implementation tasks report:
- files changed
- what changed
- tests run and results
- `git diff --check`
- `git status --short`
- anything not verified
- anything intentionally left unchanged

Do not claim success if validation is incomplete.