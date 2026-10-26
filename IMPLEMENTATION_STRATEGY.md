# Implementation Strategy

This document explains how Kitchen Table is intentionally implemented.

It is not a setup guide. It is the engineering strategy for keeping the product coherent as it grows.

## Core Strategy

### One database, two runtimes

The Python backend and the Next.js frontend both write to the same SQLite database.

This is intentional.

SQLite is the shared contract for:

- imports
- approval state
- learned mappings
- reporting
- settlement
- repair operations

Do not treat the frontend and backend as separate systems that happen to share a file. They are two execution environments over one operational model.

## Responsibility Boundaries

### Python should own data wrangling

Put these concerns in Python unless there is a strong reason not to:

- file parsing
- statement normalization
- trusted-import preservation
- ambiguous-import staging policy
- duplicate detection
- bulk repair logic

Reason:

Python is the cleaner place for batch transformation and import reasoning.

### Next.js + tRPC should own operator workflows

Put these concerns in the frontend/server app:

- staging and approval UX
- review dashboards
- settlement/payment entry
- inline taxonomy creation
- row repair UX
- export controls

Reason:

These are interactive operator workflows, not backend batch processes.

### SQLite should own truth, not the browser

Derived fields that matter to the household story should be persisted, not just displayed:

- `is_approved`
- `approved_at`
- `category_id`
- `subtype_id`
- `merchant`
- `original_input_name`
- `split`
- `member_a_spend`
- `member_b_spend`
- `track_with_friends`
- `log_to_work`

If a field is operationally meaningful, it should survive reloads, exports, and later repair.

## Workflow Strategy

### Import -> Stage -> Approve -> Review -> Repair -> Export

That is the product pipeline.

Do not short-circuit it casually.

#### Import

Goal:

- get rows into the shared DB with correct raw evidence and source metadata

#### Stage

Goal:

- keep new non-canonical rows out of reporting until a human approves them

#### Approve

Goal:

- apply household meaning
- teach the system category/subtype/canonical merchant mappings

#### Review

Goal:

- answer practical questions with approved data only

#### Repair

Goal:

- fix bad history surgically without dropping into ad hoc SQL

#### Export

Goal:

- return clean household-format data back out of the system

## Reporting Strategy

### Expense date is the reporting date

Review should use spending date, not upload date.

This is a core product rule, not a presentation detail.

### Three review tabs, but not three equal dashboards

The review layer is intentionally split into:

- `Spending`
- `Work`
- `Tags`

But those tabs should not all be treated as equally broad.

#### Spending

The main BI surface.

It should answer:

- where the money went
- how it changed over time
- who is carrying what share
- what the category mix looks like

#### Work

A narrower operational lens.

It should answer:

- what has been marked reimbursable
- where work spend is concentrated
- what reimbursement review remains

#### Tags

A lighter discovery lens.

It should answer:

- how a trip/project/event tag behaved over time
- what category mix exists inside a tag

It should not become a second full taxonomy system.

## Taxonomy Strategy

### Categories are stable

Categories and subtypes should be treated as the primary reporting structure.

They should be:

- canonical
- deduped
- intentionally maintained

### Tags are flexible

Tags should remain lighter and more ad hoc than categories.

They are useful for:

- trips
- conferences
- projects
- one-off contexts

They should not replace category structure.

### Merchant normalization should preserve evidence

Always maintain the distinction between:

- raw input text
- canonical merchant name

Learned mappings should attach to the raw imported pattern, not the already-cleaned display value.

## Allocation Strategy

### Split is not enough by itself

The household story requires:

- spender
- split
- member A/B derived allocation
- friend-sharing deductions

### Friend sharing modifies the household remainder

Friend shares are not just notes.

The strategy is:

1. total transaction amount
2. subtract friend shares
3. allocate the household remainder between the two household members

That is why friend sharing is persisted structurally now.

## UX Strategy

### Calm first

The operator should not be hit with maximum information density all at once.

Use:

- compact cards by default
- inline expansion for detailed edits
- tabs for orthogonal review modes
- suggestions only when they reduce friction

### Keep actions close to context

Examples:

- validation edits happen on the card being approved
- fixer edits happen inline on the expanded row
- category/subtype creation happens where the user is blocked

### Do not fake intelligence

Suggestions should be:

- deterministic
- explainable
- based on historical data

Avoid “AI-feeling” behavior where simple lookup or similarity ranking is enough.

## Evolution Strategy

When adding a new feature, ask:

1. Is this import logic, operator workflow, or persisted model?
2. Does it belong in Python, tRPC, or the UI?
3. Does it preserve approval gating?
4. Does it preserve the distinction between raw evidence and canonical meaning?
5. Does it reduce operator work without hiding uncertainty?

If the answer to those questions is unclear, the design is probably not ready yet.
