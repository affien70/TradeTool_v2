# TradeTool V2 — MASTERPLAN.md

> **Audience:** GitHub Copilot / coding agents working in TradeTool V2.  
> **Plan status:** ACTIVE — version 1.0  
> **Dated:** 22 September 2026  
> **Repository:** `/Users/affien/DEV/TradeTool-v2`  
> **Branch:** `main`  
> **Historical approved checkpoint:** `b43dea9` — `Switch Screener charts to Plotly`  
> **Reference V1 repository:** `/Users/affien/DEV/TradeTool` — **READ ONLY**  
> **Primary universes:** `NORWAY_V2` and S&P 500

This file is the canonical implementation plan for TradeTool V2. It is intentionally explicit so an agent can execute narrow tasks without reconstructing architecture or product intent from chat history.

## Agent execution contract — READ THIS FIRST

### Precedence and interpretation

1. Read and obey `AGENTS.md` before implementation.
2. Use the user's current explicit task as the immediate scope.
3. Use this `MASTERPLAN.md` as the product/architecture/sequencing constraint.
4. Use the relevant approved V2 specification document for detailed module contracts.
5. Use tests as executable behavior contracts.
6. Use V1 only as a read-only parity/UX oracle where this plan says V1 behavior should be preserved.
7. If these sources conflict or the requested behavior is ambiguous, **STOP and report the conflict instead of inventing a resolution**.

A later explicit user-approved decision may supersede this file. Do not silently reinterpret the plan from implementation convenience. If a structural decision changes, update the plan only when explicitly asked.

### Historical checkpoint warning

`b43dea9` is the approved Screener checkpoint that this plan was written against. It is a historical anchor, **not** an instruction to reset, checkout, revert, or detach the repository. Always inspect the current branch/status before work and preserve newer approved commits.

### Current authorized workstream

**NOW: Phase P4 — Holdings parity, starting with H1 only.**

H1 means:
- build a deterministic V1 Holdings parity fixture / expected-result contract;
- use V1 read-only as the oracle;
- no Streamlit implementation;
- no V2 production database/schema change;
- no Screener/ranking change;
- no benchmark migration decision by the agent.

After H1 is reviewed/approved, proceed to H2 only when explicitly instructed.

### Hard agent prohibitions

Unless the current user instruction explicitly authorizes it:

- Do **not** modify V1.
- Do **not** use V1 `.venv` for V2.
- Do **not** run V2 validation with system Python; use `/Users/affien/DEV/TradeTool-v2/.venv`.
- Do **not** invent business rules, schemas, APIs, data semantics, candidate thresholds, signal semantics, files, or migrations.
- Do **not** change Screener ranking/policy while working on Holdings.
- Do **not** put business logic into Streamlit pages.
- Do **not** add a second implementation of Holdings SELL/HOLD/FØLG MED logic.
- Do **not** relax data-quality validation merely to make a test/refresh pass.
- Do **not** introduce Vega-Lite, Altair, or Streamlit built-in chart APIs for application charts; application charts are Plotly.
- Do **not** perform broad refactors as part of a narrow feature/fix.
- Do **not** commit or push unless explicitly requested.
- Do **not** edit `AGENTS.md` or this `MASTERPLAN.md` unless explicitly requested.
- Do **not** resurrect failed/parked ranking approaches without a new approved experiment and decision gate.

### Expected agent task loop

For each implementation task:

1. Inspect only the code/contracts needed for the task.
2. State the narrow implementation boundary and any ambiguity.
3. Make the smallest change that satisfies the approved contract.
4. Run focused tests first using V2 `.venv`.
5. Run broader validation only when appropriate for the scope/checkpoint.
6. Run `compileall` and `git diff --check` before a commit checkpoint.
7. For UI work, require real runtime/visual inspection before commit.
8. Report exact files changed, behavior changed, tests run, and `git status --short`.
9. Stop. Do not continue into the next phase automatically.

### Communication/cost discipline

Keep agent responses short and operational. Do not produce long design essays unless requested. Prefer code inspection and focused tests over repeated speculative reasoning. Reuse this file and `AGENTS.md` instead of restating project policy in every prompt.

---

# Revision summary

<table>
<colgroup>
<col style="width: 100%" />
</colgroup>
<thead>
<tr class="header">
<th><p><strong>What is new in this masterplan</strong></p>
<p>The V1 Holdings audit materially improves the execution plan. V1
Holdings is now an explicit functional and UX reference, and Holdings is
migrated through H1-H6 before the final ranking/ML promotion gates. This
is a sequencing refinement, not a change to the core V2
strategy.</p></th>
</tr>
</thead>
<tbody>
</tbody>
</table>

| **Area**        | **Decision now locked**                                                                                                         |
|-----------------|---------------------------------------------------------------------------------------------------------------------------------|
| V1 role         | Read-only reference for working UX and Holdings behavior. Never modify or run V2 work through the V1 environment.               |
| UI architecture | UI and business engines remain strictly separated. Each Streamlit page is isolated in Pages.                                    |
| Charts          | All application charts use Plotly unless explicitly approved otherwise. Future Holdings charts follow the same rule.            |
| Screener        | Current incumbent baseline stays in place until a challenger wins a measured decision gate.                                     |
| Holdings        | Migrate by parity: fixture -\> core/service -\> persistence/settings -\> parity gate -\> UI -\> report/email.                   |
| ML/research     | Research remains outside runtime. Deleted historical ML runs are not canonical; decision-critical results must be reproducible. |
| Agent workflow  | Read before build, audit before implementation, narrow tasks, explicit stop criteria, focused tests first.                      |

## How to use this document

- Use this `MASTERPLAN.md` as the canonical project plan and sequencing document.

- Use `AGENTS.md` as the operational behavior contract for development
  agents.

- Use the detailed V2 specification documents for module contracts when
  they are consistent with this masterplan.

- Use tests as executable contracts. A passing test does not override an
  explicitly approved product decision.

- Use the V1 source only as a read-only parity and UX reference; do not
  copy its architecture blindly.

- When an approved structural decision changes, update this masterplan
  rather than letting the new plan live only in chat history.

# Contents

1\. Product objective and scope

2\. Non-negotiable principles

3\. Current project status - 22 September 2026

4\. Target architecture and module boundaries

5\. Data, universes, benchmark and refresh strategy

6\. Screener and ranking strategy

7\. Holdings migration plan H1-H6

8\. UI and chart standards

9\. Research and ML strategy

10\. Tests, decision gates and stop/revert rules

11\. Development workflow and agent cost control

12\. Performance and technical-debt backlog

13\. Future modules and parking lot

14\. Production-ready definition of done

15\. Revised execution roadmap from today

16\. Open decision register

Appendix A. Key project checkpoints

Appendix B. Sanity basket and validation references

Appendix C. Plan maintenance and change log

# 1. Product objective and scope

TradeTool V2 is a private decision-support application for finding and
monitoring plausible stock candidates, initially for Norway/OSE and the
S&P 500. It is not an automated trader and it is not intended to act as
an AI oracle. The human user makes the final decision.

## 1.1 Product objective

- Produce a small, understandable list of plausible candidates rather
  than a large opaque ranking dump.

- Use transparent market-data foundations: price, volume, trend,
  relative strength, liquidity, volatility and drawdown.

- Explain why a stock ranked highly, why it was rejected, and what risk
  flags apply.

- Support a monthly buying workflow while allowing frequent Holdings
  checks after market close.

- Build a baseline that can be measured against professional
  stock-picking quality, then allow ML only if it demonstrably improves
  the baseline.

- Preserve working V1 behaviors where they are already good, especially
  Holdings UX and behavior.

## 1.2 Target user flow

| **Step**                   | **Required behavior**                                                                        |
|----------------------------|----------------------------------------------------------------------------------------------|
| 1\. Select universe        | NORWAY_V2 or S&P 500. Universe identity must be explicit and reproducible.                   |
| 2\. Validate coverage      | Report input count, fetched count, enough-history count, scoreable count and reject reasons. |
| 3\. Build eligible set     | Apply explicit data-quality and execution eligibility rules. No hidden UI filters.           |
| 4\. Rank candidates        | Use the approved baseline or explicitly promoted challenger.                                 |
| 5\. Apply practical policy | Separate raw rank from trade suitability and risk context.                                   |
| 6\. Classify and explain   | Candidate type, trade signal, risk flags and plain-language explanation.                     |
| 7\. Manual decision        | The user decides whether to buy, wait, ignore or add to an existing position.                |
| 8\. Track in Holdings      | Monitor position, cost basis, risk, signal, realized result and reporting.                   |

## 1.3 Explicit non-goals

- No autonomous order execution or automatic portfolio trading.

- No training the model to imitate expert recommendation lists.

- No ownership penalty: an already-held stock may still rank first and
  may still be a valid additional purchase.

- No large US extended universe, news/AI world analysis, valuation
  engine or fundamental-data expansion before the core product passes
  its gates.

- No broad rewrite simply because a cleaner architecture is imaginable.
  Preserve proven assets where possible.

# 2. Non-negotiable principles

| **Principle**               | **Operational meaning**                                                                                                                                                                |
|-----------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| No patch loops              | Every material change requires a baseline, a measurable acceptance criterion and a decision gate. If the metric does not improve, stop, revert, park or change strategy.               |
| V1 is a read-only reference | Use V1 to understand working behavior and UX. Never modify the V1 repository or use its environment for V2.                                                                            |
| Engine/UI separation        | The engine owns validation, features, eligibility, ranking, policy, classification, risk and explanation. UI only renders engine outputs and sends user input through a clear adapter. |
| Pages are isolated          | Each Streamlit page must live separately. Do not mix Screener, Holdings, Diagnostics and Settings implementation into one page module.                                                 |
| One production flow         | Legacy Momentum and experimental/research tools must not be part of the normal user path.                                                                                              |
| Research outside runtime    | Backtests, audits, diagnostics and model experiments live outside production runtime. Only explicitly promoted artifacts/configurations can become production dependencies.            |
| Transparent coverage        | Universe and data coverage are first-class product outputs, not invisible implementation details.                                                                                      |
| Explain every outcome       | High rank, rejection, caution and risk must be explainable from documented data and policy.                                                                                            |
| Current data required       | Model and backtest evidence must be refreshed as 2026 progresses. Old artifacts cannot remain the permanent production basis.                                                          |
| Plotly-only app charts      | Application charts use Plotly. No Vega-Lite, Altair or Streamlit built-in chart APIs unless explicitly approved.                                                                       |
| Narrow change discipline    | One narrow change, focused tests, review, then commit. Do not bundle broad refactor + ranking + UI + schema changes.                                                                   |
| Human confidence matters    | Technical validity is not enough. Production readiness requires candidate quality that the user can inspect and understand.                                                            |

# 3. Current project status - 22 September 2026

<table>
<colgroup>
<col style="width: 100%" />
</colgroup>
<thead>
<tr class="header">
<th><p><strong>Current approved checkpoint</strong></p>
<p>V2 main is pushed at commit b43dea9: "Switch Screener charts to
Plotly". The last validation at that checkpoint passed 497 tests,
`compileall` and `git diff --check`.</p></th>
</tr>
</thead>
<tbody>
</tbody>
</table>

## 3.1 What is already in good shape

- Standalone V2 repository with separate Python environment. V2 uses
  /Users/affien/DEV/TradeTool-v2/.venv; do not share V1 .venv.

- V2 market-data contract separates raw OHLC from `adjusted_close` rather
  than mixing close semantics.

- Read-only coverage diagnostics, refresh readiness, Yahoo dry-run,
  temporary-DB write validation and benchmark diagnostics have been
  built and tested.

- OSEBX.OL is the validated V2 Norway benchmark for the Screener/data
  path, after ^OSEAX showed stale coverage.

- Full Norway sanity work documented a 293-stock universe, 289 fetched
  tickers and 274 feature-complete/ranked names in the referenced
  validation snapshot.

- Current Screener has a functioning baseline/incumbent ranking path and
  explicit risk presentation.

- Screener UI is now Plotly-based with a 60/40 layout: price/SMA/volume
  on the left and stock-vs-benchmark performance on the right.

- V1 project source is available as a read-only reference. V1 Holdings
  has been audited in detail.

## 3.2 What is not yet production-ready

| **Area**              | **Status** | **Why it is still blocked**                                                                                                                    |
|-----------------------|------------|------------------------------------------------------------------------------------------------------------------------------------------------|
| Candidate quality     | OPEN       | The current incumbent is a usable baseline, but final holdout/backtest and production promotion gates are still required.                      |
| ML challenger         | OPEN       | ML must be refreshed on current data and beat the baseline robustly before promotion. Previous experiments do not justify automatic promotion. |
| Holdings in V2        | NOW        | V1 behavior is understood, but V2 still needs controlled parity migration H1-H6.                                                               |
| Persistence/settings  | OPEN       | V2 price data exists, but Holdings schema/settings migration requires an explicit design decision.                                             |
| Integrated pilot      | BLOCKED    | Do not call the app pilot-ready until ranking quality and Holdings parity are both validated.                                                  |
| Performance hardening | LATER      | Known SQL/readiness/Yahoo batching improvements exist, but they should not derail the core flow.                                               |

## 3.3 Current Screener presentation standard

- Left column approximately 60%: Plotly price chart with Kurs, SMA50 and
  SMA200 plus a daily volume panel (about 80/20 inside the figure).

- Right column approximately 40%: selected stock and benchmark shown as
  percentage performance from the existing common baseline, with 0%
  reference line.

- The old cumulative RS subplot was removed because it duplicated
  information already visible in the normalized comparison.

- Default chart period and period selector behavior remain part of the
  existing chart-data contract; indicator history must be computed from
  sufficient pre-period data.

# 4. Target architecture and module boundaries

The core architectural rule is simple: presentation may never become a
second business engine. Every decision that can change which stock is
selected, how it is classified, or what signal it receives belongs below
the UI boundary.

## 4.1 Layered architecture

| **Layer**                 | **Owns**                                                                                                                 | **Must not own**                                                                                   |
|---------------------------|--------------------------------------------------------------------------------------------------------------------------|----------------------------------------------------------------------------------------------------|
| Streamlit Pages           | Layout, controls, tables, Plotly rendering, user interaction.                                                            | Ranking formulas, eligibility rules, signal logic, ad-hoc filtering or persistence business rules. |
| UI adapters / view models | Transform engine results into stable display contracts.                                                                  | New investment decisions or duplicated calculations.                                               |
| Domain engines/services   | Universe validation, features, eligibility, ranking, policy, classification, explanation, Holdings calculations/signals. | Streamlit imports and page-specific rendering.                                                     |
| Persistence/data access   | SQLite reads/writes, market-data contracts, transaction persistence, settings persistence.                               | Ranking decisions or UI assumptions.                                                               |
| Research/diagnostics      | Backtests, audits, experiments, reports and challengers.                                                                 | Normal production runtime side effects.                                                            |
| Artifact promotion        | Explicitly register approved model/config artifacts for runtime.                                                         | Implicit use of random local research output.                                                      |

## 4.2 Core V2 modules

- Universe and coverage module.

- Market-data ingestion/readiness module.

- Feature-generation module.

- Eligibility/execution-policy module.

- Ranking engine: incumbent baseline plus challenger interfaces.

- Candidate classification, practical trade signal, risk and explanation
  module.

- Holdings core/service module.

- Holdings persistence/settings adapter.

- Minimal Streamlit pages: Screener, Beholdning, Innstillinger;
  Diagnostics stays outside normal user flow.

- Research/training/backtest tooling outside runtime.

- Artifact/config promotion and rollback mechanism.

## 4.3 V1 reuse policy

<table>
<colgroup>
<col style="width: 100%" />
</colgroup>
<thead>
<tr class="header">
<th><p><strong>Preserve behavior, not architecture</strong></p>
<p>V1 contains proven behavior, especially in Holdings, but V1 modules
mix UI, DB access and domain logic more than V2 should. Port or wrap the
behavior behind V2 contracts; do not blindly copy module
structure.</p></th>
</tr>
</thead>
<tbody>
</tbody>
</table>

# 5. Data, universes, benchmark and refresh strategy

## 5.1 Production universes

| **Universe** | **Status** | **Rules**                                                                                                                             |
|--------------|------------|---------------------------------------------------------------------------------------------------------------------------------------|
| NORWAY_V2    | Primary    | Full input count, Yahoo/data coverage, enough-history count, feature-complete count and reject reasons must be visible.               |
| S&P 500      | Primary    | Same contracts and decision metrics as Norway. Comparisons must use equivalent dates and methodology.                                 |
| US_EXTENDED  | Later      | Do not enable before microcap/instrument-type/liquidity/survivorship/ticker-change guardrails and performance architecture are ready. |

## 5.2 Market-data contract

- Keep `raw_open`, `raw_high`, `raw_low`, `raw_close` and `adjusted_close`
  separate. Do not reintroduce a mixed-close legacy contract.

- Volume is part of the market-data record and may be surfaced in charts
  without a second database query.

- Invalid rows are diagnosed and skipped/blocked according to explicit
  validation policy; validation must not be silently relaxed to make a
  refresh pass.

- As-of date caps are mandatory for diagnostics, features and holdout
  work. No future rows may leak into a historical snapshot.

- Price discontinuity guards are data-quality/research hygiene. They are
  not automatically ranking signals.

## 5.3 Benchmark policy

| **Context**           | **Benchmark decision**                                                                                                                                                           |
|-----------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| V2 Screener / ranking | OSEBX.OL for Norway is the currently validated Yahoo benchmark. S&P 500 uses the approved US benchmark in the data contract.                                                     |
| Charts                | Stock and benchmark must share a common trading-date baseline. Performance presentation can convert indexed values to percent by indexed - 100.                                  |
| Holdings migration    | OPEN DECISION: V1 used ^OSEAX for Norway. H3/H4 must explicitly decide whether Holdings preserves V1 benchmark behavior or adopts OSEBX.OL. Do not let an agent choose silently. |

## 5.4 Database strategy

- SQLite remains acceptable for current V2 use. Do not migrate database
  technology simply for architectural neatness.

- The V2 market-data database and future Holdings persistence must have
  explicit ownership and contracts. No implicit schema repair during
  ordinary reads.

- Any Holdings schema migration must be idempotent, backed up, testable
  on temporary copies and reversible.

- Before a large US_EXTENDED universe, prioritize WAL, freshness
  metadata, fewer per-ticker calls and batch history access.
  DuckDB/parquet may be appropriate for analytical/backtest workloads;
  PostgreSQL only if concurrency/server/API needs justify it.

# 6. Screener and ranking strategy

## 6.1 Current incumbent

The current V2 incumbent is the simple RS6m-based baseline used by the
runtime/diagnostics path. Treat it as an incumbent benchmark, not as a
final production winner. It remains in place until another method wins
the same-data decision gate.

## 6.2 Required Screener flow

1.  Load the selected universe and report coverage.

2.  Build an eligible set from valid point-in-time market data.

3.  Compute approved features without leakage.

4.  Rank candidates using the incumbent or explicitly selected
    challenger.

5.  Apply practical trade policy separately from raw rank.

6.  Assign target candidate type and trade signal where the policy is
    approved.

7.  Attach risk flags, reject reasons and explanation.

8.  Display the result; do not alter rank because the user already owns
    the stock.

## 6.3 Target candidate semantics

| **Candidate type** | **Meaning**                                                                    |
|--------------------|--------------------------------------------------------------------------------|
| Stable Leader      | Strong, confirmed trend/relative strength without excessive practical risk.    |
| Early Breakout     | Beginning of a plausible momentum phase; not yet heavily extended.             |
| Extended Runner    | Strong trend but stretched/high-risk; typically caution rather than blind BUY. |
| Rebound Case       | Recovery setup that requires careful risk/context handling.                    |
| Reject             | Does not meet the practical candidate contract or has disqualifying issues.    |

**Target practical signals remain BUY / WATCH / REVIEW / AVOID. Raw
model/ranking score and practical trade signal must remain distinct
concepts.**

## 6.4 Candidate-quality gate

- Compare all contenders on identical universes, dates, costs and
  forward-return definitions.

- Measure top-N plausibility, top-vs-bottom future-return spread,
  drawdown, turnover/costs and stability across periods/universes.

- Audit manual/focus tickers and external expert lists for explanation
  quality, not as training labels.

- If a ranking change does not measurably improve the agreed metric, do
  not stack another policy patch on top of it.

- Do not revive failed challenger designs simply because they are more
  sophisticated. A challenger must earn promotion with evidence.

# 7. Holdings migration plan H1-H6

<table>
<colgroup>
<col style="width: 100%" />
</colgroup>
<thead>
<tr class="header">
<th><p><strong>Why Holdings moved earlier in the plan</strong></p>
<p>The V1 audit showed that Holdings is already a mature subsystem with
valuable behavior and UX. It can be migrated independently of the final
Screener ranking choice, so V2 should recover this core workflow now
without declaring the ranking production-ready.</p></th>
</tr>
</thead>
<tbody>
</tbody>
</table>

## 7.1 V1 Holdings behavior to preserve

- Nordnet transaction import with robust normalization and idempotent
  deduplication.

- Transaction identity through Nordnet transaction ID when present and
  deterministic fallback transaction_key when necessary.

- Open-position reconstruction with quantity and cost basis after buys,
  sales and transfers.

- Explicit cost-basis states: known, partially unknown and unknown.
  Never invent a cost basis for transferred shares without price
  history.

- Realized result and dividend reporting.

- Shared HOLD / FØLG MED / SELL signal engine used by UI and daily
  report/email.

- Refined confirmation behavior: an isolated fast-SMA or ATR break can
  be FØLG MED when trend/RS/momentum remain strong, while confirmed
  weakness can escalate to SELL.

- Harder risk exits such as SMA200 failure and configured cost-stop
  remain part of the parity target where enabled by settings.

- Stored settings are data. V1 database settings may differ from code
  defaults and must not be discarded during migration.

- V1 UX elements worth preserving: active positions, value/P&L,
  selectable detail, purchase markers, GAV, peak, stop/reference levels,
  freshness, realized-result views, dividends and trade timeline.

## 7.2 H1 - V1 Holdings parity fixture

**STATUS: NEXT STEP**

Goal: create a testable behavioral contract before writing production
Holdings code in V2.

- Build sanitized deterministic fixtures that represent the important V1
  transaction cases and settings.

- Capture expected imported transaction identities, active positions,
  quantities, cost-basis status, realized result, dividends and
  signals/reasons.

- Include regression cases for the refined FØLG MED vs SELL behavior.

- Use V1 source/results read-only as the oracle. Do not write to V1 DB
  or repository.

- No Streamlit work and no V2 production-schema change in H1.

<table>
<colgroup>
<col style="width: 100%" />
</colgroup>
<thead>
<tr class="header">
<th><p><strong>H1 gate</strong></p>
<p>Fixture assertions reproduce approved V1 behavior and can run
independently of live user data. If the expected behavior is ambiguous,
stop and resolve the contract before implementation.</p></th>
</tr>
</thead>
<tbody>
</tbody>
</table>

## 7.3 H2 - Holdings core/service

**STATUS: AFTER H1**

- Implement or port pure V2 domain logic behind a stable service
  contract.

- No Streamlit imports in the Holdings core.

- Own import parsing/deduplication, position reconstruction, cost basis,
  realized result, dividends and signal evaluation in the core/service
  layer.

- Return one structured analysis result that UI and reports can both
  consume.

- Reuse proven V1 logic where sensible, but remove V1 architectural
  coupling rather than copying it.

<table>
<colgroup>
<col style="width: 100%" />
</colgroup>
<thead>
<tr class="header">
<th><p><strong>H2 gate</strong></p>
<p>All parity fixtures pass in pure unit/service tests. No UI or
production database change is required to prove the core
behavior.</p></th>
</tr>
</thead>
<tbody>
</tbody>
</table>

## 7.4 H3 - Persistence and settings adapter

**STATUS: DECISION REQUIRED**

- Choose the V2 Holdings persistence schema explicitly. Do not let an
  agent invent it ad hoc.

- Choose the migration path: re-import source transactions, controlled
  copy/migration, or another explicit approved method.

- Preserve transaction identity/deduplication semantics and cost-basis
  state.

- Preserve settings as persistent data. Define
  schema/defaults/current-value migration explicitly.

- Resolve the Norway benchmark decision for Holdings (^OSEAX parity vs
  OSEBX.OL V2 standard).

- All write tests run first against temporary/copy databases with
  rollback/backup procedures.

<table>
<colgroup>
<col style="width: 100%" />
</colgroup>
<thead>
<tr class="header">
<th><p><strong>H3 gate</strong></p>
<p>Schema, migration and benchmark decisions are approved before any
production migration. Migration is idempotent and reversible.</p></th>
</tr>
</thead>
<tbody>
</tbody>
</table>

## 7.5 H4 - Behavioral parity gate

**STATUS: AFTER H3**

- Run the same transaction fixture/snapshot through V1 reference
  behavior and V2 Holdings core.

- Compare active instruments, quantities, known/partial/unknown cost
  basis, realized P/L, dividends, signal and reason text/semantics.

- Any intentional difference must be documented as an approved V2
  change, not dismissed as implementation drift.

- If benchmark migration changes a signal, quantify and approve the
  difference explicitly.

<table>
<colgroup>
<col style="width: 100%" />
</colgroup>
<thead>
<tr class="header">
<th><p><strong>H4 gate</strong></p>
<p>Parity passes or every deviation has a written approved reason. No UI
work can hide a core parity failure.</p></th>
</tr>
</thead>
<tbody>
</tbody>
</table>

## 7.6 H5 - Beholdning UI

**STATUS: AFTER H4**

- Recreate the good V1 workflow through V2 adapters rather than
  embedding business logic in the page.

- Use a dedicated Streamlit Pages module.

- Use Plotly for all Holdings charts.

- Primary chart standard: price + SMA/volume with Holdings overlays such
  as purchase points, GAV, peak and applicable stop/reference lines.

- Keep active-position table, totals/P&L, detail selection, freshness
  and realized/dividend views concise and readable.

- Do not redesign a working V1 behavior unless a measured defect or
  approved usability improvement justifies it.

<table>
<colgroup>
<col style="width: 100%" />
</colgroup>
<thead>
<tr class="header">
<th><p><strong>H5 gate</strong></p>
<p>Visual/functional review confirms V1-level usefulness while V2
architecture stays clean. No ranking or Screener changes are
bundled.</p></th>
</tr>
</thead>
<tbody>
</tbody>
</table>

## 7.7 H6 - Daily report / email parity

**STATUS: FINAL HOLDINGS STEP**

- Report/email must consume the same Holdings result/signal object as
  the UI.

- No duplicate SELL logic in the reporting script.

- Verify signal and reason parity between UI, export/report and email
  fixtures.

- Schedule/deployment changes happen only after local logic is stable
  and approved.

<table>
<colgroup>
<col style="width: 100%" />
</colgroup>
<thead>
<tr class="header">
<th><p><strong>H6 gate</strong></p>
<p>Same position -&gt; same signal -&gt; same reasons across UI and
report/email. No split-brain business logic.</p></th>
</tr>
</thead>
<tbody>
</tbody>
</table>

# 8. UI and chart standards

## 8.1 Page boundaries

- Screener page: candidate table, selected-candidate detail, Plotly
  charts and explanation only.

- Beholdning page: Holdings adapter output only; no duplicated signal or
  persistence logic.

- Innstillinger page: explicit user-facing configuration/update
  controls.

- Diagnostikk: developer/research information only. It should not become
  a normal production workflow or a second way to make stock decisions.

## 8.2 Plotly standard

| **Area**        | **Standard**                                                                                                               |
|-----------------|----------------------------------------------------------------------------------------------------------------------------|
| Renderer        | Plotly only for application charts unless explicitly approved otherwise.                                                   |
| Screener layout | Two columns approximately 60/40.                                                                                           |
| Screener left   | Price + SMA50 + SMA200, daily volume below, shared date axis, roughly 720px total height.                                  |
| Screener right  | Selected stock and benchmark as % performance from common baseline; 0% reference line; no duplicate cumulative RS subplot. |
| Holdings future | Use the same visual language, adding Holdings-specific overlays rather than a separate chart technology.                   |
| Hover/legend    | Useful and uncluttered. Do not repeat information that is already clear from the chart.                                    |
| Data ownership  | Figure builders consume prepared data; charts do not become business-rule engines.                                         |

## 8.3 UX principles

- The user should understand a candidate within a few seconds: trend,
  market-relative performance, volume, risk and explanation.

- Prefer visible facts over hidden toggles and hidden filters.

- Avoid redundant indicators. If two charts tell the same story, keep
  the clearer one.

- Do not sacrifice readability to show every research metric.

- Keep user-facing terminology stable and explain the difference between
  model/rank and practical trade signal.

# 9. Research and ML strategy

## 9.1 Baseline first

ML is a challenger, not the default winner. The baseline must remain
reproducible and available for every comparison. A more complex model is
not an improvement unless it wins the agreed decision metrics.

## 9.2 ML requirements before promotion

- Strict point-in-time data and walk-forward/expanding-window
  validation.

- No feature leakage, reporting-lag leakage or future benchmark
  information.

- Training/backtest data refreshed with newer 2026 observations before
  final production promotion.

- Compare baseline and challenger on identical universes, dates, costs
  and forward-return targets.

- Promote based on median/robust performance, drawdown, top-vs-bottom
  spread, turnover/costs and stability - not the prettiest single
  backtest.

- Persist clear metadata for model/config/data version and promotion
  decision.

## 9.3 Research artifact policy

- Research scripts and outputs remain outside runtime and normal UI.

- Only explicitly promoted artifacts/configs may be consumed by
  production.

- Historical ML runs/reports that were removed to reduce project-source
  size are not canonical. If a result matters for a future decision,
  regenerate it reproducibly from current code/data or record that it
  cannot be reproduced.

- Do not depend on random local research side-state.

## 9.4 Later fundamental/forward-looking features

- Only after the core ranking/holdings flow passes gates, consider
  point-in-time business signals such as revenue/margin acceleration,
  estimate revisions, order intake/backlog, CAPEX productivity,
  contracts/customers, free-cash-flow improvement, deleveraging and
  capacity/hiring expansion.

- SEC EDGAR is the preferred future source candidate for
  CAPEX/fundamental inputs where appropriate and available.

- The ML target remains future excess return / ranking quality. Do not
  train on expert recommendation labels.

- Strict reporting lags, leakage controls, missing-data treatment and
  robustness tests are mandatory before any fundamental input enters
  production.

# 10. Tests, decision gates and stop/revert rules

## 10.1 Test pyramid for each change

1. Read/audit the relevant code and contract first.
2. Add or update the narrowest focused unit/service tests.
3. Run focused tests with the V2 `.venv`.
4. Run full discovery only at an appropriate checkpoint or after broad/core changes.
5. Run `compileall` and `git diff --check` before commit.
6. For UI changes, perform a real visual/runtime inspection before commit.
7. Commit only the approved narrow change; push separately.

## 10.2 Mandatory decision metrics

| **Metric**                         | **Why it matters**                                                                                |
|------------------------------------|---------------------------------------------------------------------------------------------------|
| Universe/data coverage             | A ranking cannot be trusted if the intended market is incompletely or inconsistently represented. |
| Top-N plausibility                 | The top list must contain candidates that make practical sense under the product goal.            |
| Known sanity cases                 | Focus tickers and expert lists reveal whether the engine can explain agreement/disagreement.      |
| Top-vs-bottom future-return spread | Measures whether the ranking contains useful separation.                                          |
| Drawdown                           | A strategy that looks good on return but produces unacceptable drawdowns is not robust.           |
| Turnover and realistic costs       | Prevents untradable paper edge.                                                                   |
| Stability                          | Results must persist across periods and universes rather than one favorable slice.                |
| Explanation/reject correctness     | The product must explain the same policy the engine actually applied.                             |
| Holdings parity/stability          | Migration must not silently change active positions or sell logic.                                |

## 10.3 Hard stop/revert rules

- If a change does not measurably improve its agreed metric, do not add
  another patch on top of it.

- If scope expands beyond the current phase, stop and reclassify the
  extra work.

- If candidate quality does not improve after the agreed ranking/model
  experiment, revert or park the approach and change strategy.

- No new external data source unless a documented core failure shows why
  it is needed.

- No broad refactor bundled with ranking, policy or UI change.

- No relaxation of data-quality validation merely to make a refresh or
  backtest pass.

- No production-ready claim until all relevant gates in Section 14 are
  satisfied.

# 11. Development workflow and agent cost control

## 11.1 Agent operating rules

- `AGENTS.md` is mandatory reading before implementation work.

- The agent is an implementation hand, not the product architect. It
  must not invent requirements, business rules, schemas, APIs, files,
  fixes or data semantics.

- When something is missing/broken/ambiguous: investigate and report
  first. Do not auto-repair by assumption.

- V1 is protected and read-only. V2 changes happen only in
  /Users/affien/DEV/TradeTool-v2.

- Use only the V2 `.venv` for V2 runtime/tests. Do not use V1 .venv or
  system Python for project validation.

- No agent use for trivial git push, simple status checks or obvious
  one-line terminal work unless needed for safety.

- Prompts should be short, precise, scoped and contain explicit STOP
  conditions.

- Read before build. Audit before implementation. Default unclear
  request = read-only audit, not code.

## 11.2 Cost-control workflow

| **Before using an agent** | **Question**                                                                      |
|---------------------------|-----------------------------------------------------------------------------------|
| Need                      | Does this task actually require an agent, or can a read/manual command settle it? |
| Scope                     | Can the task be reduced to one narrow implementation unit?                        |
| Contract                  | Is expected behavior already clear from plan/tests/V1 reference?                  |
| Stop criterion            | What exact condition tells the agent to stop rather than keep exploring?          |
| Validation                | What focused tests can prove the change before a full suite?                      |

## 11.3 Git/change discipline

- Keep the repo clean between tasks.

- One narrow change -\> tests -\> visual/review -\> commit -\> push.

- Do not commit generated research noise or random local state into the
  production path.

- Do not create new directories/files in production-server
  troubleshooting unless explicitly approved.

- Document important checkpoints and decisions so a later conversation
  can recover the state without archaeology.

# 12. Performance and technical-debt backlog

<table>
<colgroup>
<col style="width: 100%" />
</colgroup>
<thead>
<tr class="header">
<th><p><strong>Do not start this backlog during H1-H6 unless it blocks
correctness</strong></p>
<p>The V2 source audit found real performance opportunities, but they
are not the current bottleneck for product completion. Fix them after
the core user flow is intact or when measurements show they block
testing.</p></th>
</tr>
</thead>
<tbody>
</tbody>
</table>

| **Backlog item**              | **Reason / intended fix**                                                                                                              | **Priority**         |
|-------------------------------|----------------------------------------------------------------------------------------------------------------------------------------|----------------------|
| Index-friendly price queries  | Avoid UPPER(TRIM(ticker)) and date(price_date) in hot SQL predicates when normalized storage/inputs allow indexed ticker/date lookups. | Soon after core flow |
| Readiness conversion overhead | Reduce repeated full feature-row conversions and benchmark transformations that are not consumed by the caller.                        | Soon after core flow |
| Yahoo batching                | Current provider path can call yfinance per ticker. Batch compatible requests to reduce latency/failure surface.                       | Soon after core flow |
| Cache/freshness metadata      | Use explicit freshness/coverage metadata to avoid unnecessary reloads and repeated per-ticker work.                                    | Soon                 |
| SQLite tuning                 | WAL, fewer per-ticker DB calls and batch reads before any large universe expansion.                                                    | Later                |
| Analytical storage            | Consider DuckDB/parquet for large backtest/research workloads, not as a premature runtime rewrite.                                     | Later                |

# 13. Future modules and parking lot

## 13.1 NOW

- H1-H6 Holdings parity migration.

- Preserve the approved Screener checkpoint and avoid unrelated
  ranking/UI changes during Holdings migration.

## 13.2 SOON - after Holdings parity

- Complete the incumbent holdout/backtest/candidate-quality decision
  gate on current enough data.

- Lock a robust production baseline/config if the baseline meets
  requirements.

- Refresh/retrain the ML challenger and compare it against the same
  baseline.

- Integrate the winning ranking path into a controlled local pilot only
  after promotion criteria are satisfied.

- Hide or separate diagnostics/research UI from the normal end-user
  flow.

## 13.3 LATER - one module at a time

- Enhanced Early Breakout module beyond the initial candidate type.

- Point-in-time fundamentals, estimate revisions, order backlog/order
  intake and Future Demand Score.

- SEC EDGAR-based CAPEX/fundamental inputs where useful.

- Fundamental valuation and short-interest modules if a measured need is
  established.

- Large US_EXTENDED universe after data-quality/model-risk/performance
  guardrails.

- DuckDB/parquet analytical path if research scale justifies it.

## 13.4 PARKING LOT

- Local AI/Ollama or other AI analysis plugin that can summarize
  world/news/context. Build as an optional plugin after the core app
  works; never entangle it with the baseline engine prematurely.

- News ingestion/scraping and global-event context.

- Automated external expert-list import. Continue using expert lists
  manually as sanity checks until there is a validated product need.

- Broader deployment/agent automation that does not directly improve the
  stock-selection workflow.

## 13.5 Explicitly not now

- No autonomous trading.

- No new ranking factor merely because one recent stock would have
  ranked better with it.

- No ML feature expansion while core candidate-quality evidence is
  unresolved.

- No redesign of working Holdings behavior for aesthetic reasons.

- No migration away from SQLite without a measured scaling/concurrency
  requirement.

# 14. Production-ready definition of done

**TradeTool V2 may be called production-ready only when all applicable
items below are satisfied. Technical "it runs" status is not enough.**

| **Area**          | **Production-ready condition**                                                                                               |
|-------------------|------------------------------------------------------------------------------------------------------------------------------|
| UI flow           | One clean normal path with Screener, Holdings and Settings; diagnostics/research do not confuse normal use.                  |
| Data              | Reproducible current market-data update; transparent enough OSE and S&P 500 coverage; invalid/missing data handled honestly. |
| Ranking           | Baseline/challenger comparison completed on identical conditions; production method/config explicitly promoted.              |
| 2026 freshness    | Decision evidence and any ML training/backtest are refreshed enough to represent current 2026 data.                          |
| Candidate quality | Top candidates are plausible across periods/universes; focus/expert cases are passed or rationally explained.                |
| Policy            | Practical trade policy, candidate semantics, risk/reject reasons and explanations are verified.                              |
| Holdings          | H1-H6 completed; V1 parity or approved deviations documented; signals stable.                                                |
| Reports           | Daily report/email uses the exact same Holdings signal source as the UI.                                                     |
| Reproducibility   | Data/config/model/artifact build and promotion can be recreated without deleted/random local side-state.                     |
| Rollback          | Documented rollback path exists for data/schema/config/model changes.                                                        |
| Tests             | Focused and full regression suites are green at release checkpoint; UI has been visually inspected.                          |
| User confidence   | The user has genuine confidence that candidates and Holdings behavior are useful enough for a controlled pilot.              |

# 15. Revised execution roadmap from today

<table>
<colgroup>
<col style="width: 100%" />
</colgroup>
<thead>
<tr class="header">
<th><p><strong>Sequencing change</strong></p>
<p>Holdings parity now comes before final ranking/ML promotion. This
completes the core product workflow using the current baseline while
keeping ranking quality explicitly unapproved until its later
gate.</p></th>
</tr>
</thead>
<tbody>
</tbody>
</table>

| **Phase** | **Workstream**                             | **Status**                           | **Gate/output**                                                                                 |
|-----------|--------------------------------------------|--------------------------------------|-------------------------------------------------------------------------------------------------|
| P0        | Specification and V2 principles            | DONE                                 | Approved V2 docs/principles; no patch-loop strategy.                                            |
| P1        | V1 reference / baseline capture            | DONE + ongoing reference             | V1 source available read-only; Holdings audit completed.                                        |
| P2        | V2 shell, contracts and data foundation    | DONE                                 | Standalone repo, coverage/readiness, `price_history_v2` contract, benchmark validation.           |
| P3        | Screener technical foundation and UI       | TECHNICALLY READY; quality gate open | Current incumbent runtime works; Plotly UX checkpoint b43dea9.                                  |
| P4        | Holdings parity H1-H6                      | NOW                                  | Next work. No ranking changes bundled.                                                          |
| P5        | Baseline candidate-quality + holdout gate  | NEXT                                 | Decide whether incumbent is strong enough and lock robust production config if justified.       |
| P6        | ML challenger refresh and comparison       | AFTER P5                             | Updated data, strict PIT/leakage controls; promote only if robustly better.                     |
| P7        | Integrated local pilot                     | AFTER P4-P6                          | Screener + Holdings + update + reporting under real usage; no production claim before evidence. |
| P8        | Performance hardening / deployment cleanup | AFTER FLOW STABLE                    | Optimize hot paths, batching, caches, service/deployment only where measured.                   |
| P9        | Later modules                              | LATER                                | Early Breakout enhancement, fundamentals, AI/news plugin, US_EXTENDED, etc.                     |

## 15.1 Immediate next three steps

1. H1: create a deterministic V1 Holdings parity fixture and expected-result contract. Read-only reference; no V2 production DB/UI change.
2. H2: implement the V2 Holdings core/service behind pure contracts until the H1 fixture passes.
3. H3: stop and obtain explicit approval for persistence/schema/settings/benchmark migration before writing production Holdings data.

# 16. Open decision register

| **ID** | **Decision**                                                                                     | **Status** | **Must be resolved before** |
|--------|--------------------------------------------------------------------------------------------------|------------|-----------------------------|
| D1     | Holdings Norway benchmark: preserve V1 ^OSEAX semantics or standardize on validated V2 OSEBX.OL. | OPEN       | H4 parity sign-off          |
| D2     | V2 Holdings persistence schema and migration method.                                             | OPEN       | H3 implementation           |
| D3     | How existing V1 settings values are migrated and which V2 defaults apply only to new installs.   | OPEN       | H3/H4                       |
| D4     | Production baseline ranking/config after holdout and candidate-quality gate.                     | OPEN       | P5 completion               |
| D5     | Whether any ML challenger earns promotion over the baseline.                                     | OPEN       | P6 completion               |
| D6     | Final production visibility/location of Diagnostics.                                             | OPEN       | Integrated pilot            |
| D7     | Pilot observation window and release checklist cadence.                                          | OPEN       | P7 start                    |

# Appendix A. Key project checkpoints

The following checkpoints are useful landmarks. They are not a
substitute for git history, but they summarize the controlled
progression toward the current state.

| **Commit** | **Checkpoint**                      | **Meaning**                                                                                      |
|------------|-------------------------------------|--------------------------------------------------------------------------------------------------|
| 08c8034    | Coverage diagnostics foundation     | Read-only SQLite/schema/universe coverage foundation.                                            |
| 5768da2    | Refresh readiness date-column fix   | Correctly recognized price_date and exposed legacy OHLC quality blocker.                         |
| bb32502    | Market-data refresh design          | Defined separate raw OHLC and `adjusted_close` contract.                                           |
| 481265d    | Yahoo dry-run dependency/fetch      | Validated live Yahoo normalization without writing market data.                                  |
| 424f616    | OHLC tolerance audit                | Measured real minor OHLC consistency violations before changing policy.                          |
| 18b45d6    | Benchmark gap diagnostics           | Established OSEBX.OL as preferred current Yahoo Norway benchmark.                                |
| e4059ad    | Partial temporary-test write mode   | Allowed invalid-row skip only in protected temp/test DB workflow.                                |
| 0354e6f    | Candidate-quality comparison report | Formalized incumbent quality diagnostics and decision output.                                    |
| 0dd3777    | Arrow display serialization fix     | Stabilized Screener table display path.                                                          |
| b43dea9    | Switch Screener charts to Plotly    | Current approved checkpoint: Plotly-only chart standard, volume, 60/40 layout, comparison chart. |

## Appendix A.1 Deleted/omitted research artifacts

Some historical ML runs and reports were intentionally removed from the
project sources to keep the source package manageable. This does not
change the plan: decision-critical model evidence must be reproducible.
A deleted historical report may be background context, but it cannot be
the only evidence supporting a production decision.

# Appendix B. Sanity basket and validation references

## B.1 Focus tickers

- SUBC.OL - important sanity/postmortem case because it was manually
  considered/bought when the app did not recommend it.

- WWI.OL, MPCC.OL and ENDUR.OL - real prior app-influenced
  holdings/trades useful for postmortem comparison.

- Current top candidates from each validation run should be inspected,
  but one stock must never dictate a ranking patch.

## B.2 External expert lists

Use expert recommendations as a manual sanity reference only. Do not
import them as labels or targets. The important question is whether
TradeTool can explain why it agrees or disagrees using its own data and
policy.

- Example 1 June 2026 keep list: MOWI, SpareBank 1 Midt-Norge,
  Storebrand, Aker BP, Vend Marketplaces.

- Example sell list: Yara International, DOF Group.

- Example buy list: Endur, Constellation Oil Services, Europris.

## B.3 Required review dimensions for sanity cases

- Universe/data presence and freshness.

- Eligibility/reject reason.

- Raw rank/score and practical trade signal.

- Relative strength and trend.

- SMA/trend position and stretch.

- Liquidity, volatility and drawdown.

- Candidate type / risk classification where applicable.

- Whether the explanation matches the actual engine inputs and policy.

# Appendix C. Plan maintenance and change log

## C.1 Source-of-truth hierarchy

1. `docs/v2/MASTERPLAN.md` - project sequencing, non-negotiable principles, phase gates and major decisions.
2. `AGENTS.md` - day-to-day operational rules for implementation agents.
3. Approved V2 module specifications - detailed contracts such as architecture, data, Screener policy, ML strategy and test gates.
4. Executable tests - behavioral contracts for implemented code.
5. V1 project source - read-only parity/UX reference, especially Holdings.
6. Research reports - evidence for a decision, never an implicit runtime contract.
7. Chat history - context only unless the decision is promoted into the plan/spec/tests.

## C.2 Change control

- New ideas are classified as NOW, SOON, LATER, PARKING LOT or NO/NOT
  NOW.

- Nothing enters NOW without replacing or completing an existing
  priority.

- If an approved structural decision changes, create a new masterplan
  revision and record the reason/date.

- Do not silently rewrite earlier decisions in implementation code.

## C.3 Change log

| **Version** | **Date**    | **Change**                                                                                                                                                                                                              |
|-------------|-------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| 1.0         | 22 Sep 2026 | Initial canonical masterplan. Consolidates V2 core plan, current b43dea9 Screener status, Plotly/UI rules, V1 read-only reference strategy, detailed Holdings H1-H6 migration, agent cost controls and revised roadmap. |

<table>
<colgroup>
<col style="width: 100%" />
</colgroup>
<thead>
<tr class="header">
<th><p><strong>Next approved action</strong></p>
<p>Start H1 only: build the V1 Holdings parity fixture/contract. No
production database change, no UI implementation and no Screener/ranking
change in that step.</p></th>
</tr>
</thead>
<tbody>
</tbody>
</table>

---

## Copilot maintenance note

Expected repository path for this file:

`docs/v2/MASTERPLAN.md`

When a major approved decision changes:
1. change only the affected sections;
2. increment the plan version/date;
3. add one concise change-log row;
4. keep completed historical gates/checkpoints intact unless they were factually wrong;
5. never rewrite history to make the current implementation appear planned in retrospect.

**Current next action remains H1 until explicitly changed by the user.**
