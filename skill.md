---
name: kitchen-table-toolkit
description: Work on the Kitchen Table stack while preserving its approval-first, validation-first household-finance model. Use when editing import logic, approval workflows, review/reporting tabs, repair tools, SQLite schema, or learned mapping behavior.
---

# Kitchen Table Toolkit

Treat this repo as a household decision system, not a generic expense tracker.

## Product Philosophy

- Approval matters. Imported rows should not quietly become reported truth.
- Trust canonical household history.
- Preserve raw input evidence and canonical cleaned meaning separately.
- Keep member A/B/friend allocation coherent.
- Prefer calm, focused operator UX over maximal forms and dashboards.
- Use deterministic suggestions instead of fake intelligence.

## Mental Model

### Python backend

Use Python for:

- statement parsing
- normalization
- categorization policy
- duplicate detection
- repair scripts

### Next.js + tRPC

Use the frontend/server stack for:

- upload orchestration
- staged approval UI
- review/reporting
- settlement
- table fixer
- export

### SQLite

SQLite is the shared contract.

When a concept matters operationally, persist it there.

## Current Product Surfaces

- `/upload`
  Import files, choose source, and see truthful import results.
- `/validation`
  Approve staged rows, create taxonomy inline, normalize merchants, set friend/household allocation.
- `/review`
  Three focused tabs:
  - `Spending`
  - `Work`
  - `Tags`
- `/fixer`
  Inline repair and deletion surface.
- `/export`
  Export back into the household sheet format.

## Rules For Changes

1. Keep the approval gate intact unless the change is specifically about canonical trusted imports.
2. Do not collapse `original_input_name` and `merchant` into one concept.
3. If changing split/allocation behavior, verify downstream `member_a_spend` and `member_b_spend`.
4. If changing validation behavior, inspect:
   - backend categorization
   - tRPC mutations/queries
   - validation UI
5. If changing reporting, ask whether the view belongs in:
   - `Spending`
   - `Work`
   - `Tags`
   instead of stuffing more into the main tab.
6. Keep tags lighter than categories.
7. Keep repair actions close to the row being fixed.

## What Good Looks Like

- fewer ambiguous rows require manual effort over time
- reports are trustworthy because they use approved data
- historical truth is preserved
- operator friction goes down
- the UI stays readable
- the model remains explainable
