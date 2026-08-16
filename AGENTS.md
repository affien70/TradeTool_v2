# TradeTool v2 — Codex Instructions

## Scope

- Codex works only inside this standalone v2 repository unless explicitly told otherwise.
- The legacy `/Users/affien/DEV/TradeTool` repository remains the protected v1 reference.
- Follow the approved specification under `docs/specification/` and baseline evidence under `evidence/v1_baseline/`.

## Phase Discipline

- Implement only the approved phase task.
- Do not guess missing business logic.
- Stop if a task would require changing v1 state, copying production databases, or loading model artifacts into runtime.

## Architectural Rules

- Use the `src/` package layout.
- Keep runtime, contracts, UI, diagnostics, and holdings boundaries explicit.
- `tradetool` packages must not import legacy v1 modules.
- Only `tradetool.ui`, `app.py`, and `pages/` may import Streamlit.

## Safety Rules

- Do not add production database paths, SMTP settings, secrets, or winning-engine selection to configuration.
- Do not implement screener formulas, holdings rules, ML scoring, or artifact promotion unless explicitly requested.
- Keep reports append-only and generated outputs out of commits unless the task explicitly asks for them.
