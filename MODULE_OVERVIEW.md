# Module Overview

This is the current repo map for Kitchen Table.

It reflects the app as it exists now:

- approval-gated reporting
- real review tabs for `Spending`, `Work`, and `Tags`
- friend-sharing allocation
- inline taxonomy creation
- row repair through the web UI

## Backend

### `backend/src/main.py`

Primary Python CLI entrypoint.

Responsibilities:

- initialize and seed the database
- process incoming files
- run import orchestration
- expose repair/status commands

Use this when the task is operational or batch-oriented.

### `backend/src/db`

Canonical SQLAlchemy-side schema and session management.

Files:

- `models.py`
- `session.py`

Use this when persisted fields or backend-side schema semantics change.

### `backend/src/ingest`

Statement and export normalization layer.

Main file:

- `csv_reader.py`

Responsibilities:

- detect file format
- normalize rows
- parse dates and amounts
- preserve trusted household rows
- reject malformed or ignorable rows

Use this when a statement imports incorrectly or a new format must be added.

### `backend/src/categorize`

Categorization and queue policy.

Main file:

- `categorizer.py`

Responsibilities:

- apply learned mappings
- decide what should be staged for approval
- preserve trusted canonical history
- avoid overconfident merchant guessing

### `backend/src/validate`

Duplicate detection.

Main file:

- `duplicate_detector.py`

Use this when duplicate handling or import idempotence is wrong.

### `backend/src/scans`

Paper scan cutting, naming, and OneDrive filing (FastFoto batches).

Files:

- `conventions.py`: learns categories, merchant history, people, cars, and manual brands from OneDrive
- `naming.py`: `YYYY-MM-DD[_HH-MM]_merchant.pdf` naming, slugs, collision-safe paths
- `cutter.py`: page order across ScanSmart files, front/back pairing, blank and separator pages, PDF writing
- `grouping.py`: joins consecutive pages into multi-page documents (page markers, same visit, continuations)
- `extract.py`: date, time, total, and merchant extraction from scan text
- `classify.py`: document kind, category, person, car, and destination folder
- `pipeline.py`: plan -> manifest -> apply -> undo
- `filedates.py`: file created/modified times from the date in the filename
- `naming_eval.py`: census scoring of names and folders against the labels in filenames and folders
- `segmentation_eval.py`: page joining scored as segmentation (dev/test split, baselines, bootstrap CIs)
- `text_index.py`: page text of filed documents in SQLite with FTS5 search, and Excel export
- `label_audit.py`: finds probably-wrong labels (names, folders, text contradictions) and records review verdicts
- `cli.py`: `scans` command group

Use this when scanned documents are named or filed wrong.

### `backend/src/ocr`

Optional local OCR adapters (Tesseract via pytesseract, or RapidOCR).

## Frontend App

### `frontend/src/app/page.tsx`

Dashboard entry page.

The dashboard is now real:

- last 12 weeks of spending context
- all-time net due
- real recent activity and review entry points

### `frontend/src/app/_components`

Shared app UI.

Important files:

- `navigation.tsx`
- `dashboard.tsx`

### `frontend/src/app/upload/page.tsx`

Upload workflow UI.

Responsibilities:

- choose file
- choose statement source
- optionally choose spender
- show truthful processing results

### `frontend/src/app/api/upload/route.ts`

Upload orchestration route.

Responsibilities:

- receive browser upload
- save file to `uploads`
- call Python processing
- translate backend output into a usable UI response

### `frontend/src/app/validation/page.tsx`

The main approval workflow.

Current responsibilities:

- review staged rows
- approve or delete rows
- choose category/subtype
- create category/subtype inline
- set canonical merchant
- use deterministic merchant/tag/category suggestions
- set split
- set friend sharing
- set `logToWork`
- set tags
- remember category/subtype/canonical merchant mappings

This is the highest-leverage operator surface in the app.

### `frontend/src/app/review/page.tsx`

Review layer.

Current responsibilities:

- `Spending` tab:
  - category lensing
  - category to subtype drilldown
  - settlement and payment logging
  - trend charts by expense date
- `Work` tab:
  - reimbursement-oriented analysis of `log_to_work` expenses
- `Tags` tab:
  - lighter discovery-oriented tag analysis

### `frontend/src/app/fixer/page.tsx`

Precision repair surface.

Responsibilities:

- inline row expansion
- edit historical row fields
- quick single delete
- multi-select delete
- sort by most recently uploaded

This is not a generic admin grid. It is the surgical repair tool.

### `frontend/src/app/export/page.tsx`

Export workflow.

Responsibilities:

- choose range
- export back into the original household spreadsheet shape

## Frontend Server

### `frontend/src/server/db.ts`

Frontend-side SQLite connector and local table bootstrap.

Current responsibilities include:

- opening the shared SQLite DB
- initializing friend-sharing helper tables when needed

### `frontend/src/server/db/schema.ts`

Drizzle schema for the shared database.

Important current concepts:

- approval state
- merchant normalization
- settlement payments
- friend sharing

### `frontend/src/server/api/root.ts`

Top-level tRPC router registry.

### `frontend/src/server/api/routers/validation.ts`

Approval/staging API.

Responsibilities:

- staged queue
- recent approved items
- approval stats
- approve mutations
- merchant/tag/category suggestion endpoints

### `frontend/src/server/api/routers/categories.ts`

Taxonomy API.

Responsibilities:

- category retrieval
- category creation
- subtype creation

### `frontend/src/server/api/routers/friends.ts`

Friend-sharing API.

Responsibilities:

- return available friends
- create new friends inline from the validation UI

### `frontend/src/server/api/routers/expenses.ts`

Broad reporting and repair API.

Current responsibilities:

- review insights for `Spending`
- work insights for `Work`
- tag insights for `Tags`
- settlement and payment logging
- table fixer row editing
- row deletion and bulk deletion

This is one of the most important routers in the frontend now.

### `frontend/src/server/api/routers/statementSources.ts`

Statement-source freshness and upload recency API.

## Shared Utility Layer

### `frontend/src/lib/expense-sharing.ts`

Friend-sharing and allocation helpers.

Responsibilities:

- normalize friend shares
- calculate household remainder
- derive member A/B allocation from the remainder
- support both percentage and exact modes

## Root-Level Docs

### `README.md`

Current project narrative and design philosophy.

### `HOW_TO_TEST.md`

Current practical test methodology.

### `IMPLEMENTATION_STRATEGY.md`

Current engineering strategy for where responsibilities belong and how the stack should evolve.

### `skill.md`

Current Codex skill for this project.
