# How To Test Kitchen Table

This repo should be tested like an operator-facing data system, not like a toy frontend.

The right testing loop is:

1. run the real UI
2. perform the real workflow
3. verify the persisted SQLite state

## Testing Philosophy

- Prefer workflow testing over isolated component confidence.
- Prefer database verification over trusting a button click.
- Prefer explicit approval-state verification over assuming imported data belongs in reporting.
- Prefer isolated DB copies for destructive tests.
- Do not pretend a repo-wide clean typecheck is the current source of truth for correctness in this repo.

## Current Practical Constraints

At the time of writing:

- the app is commonly run on `http://localhost:3010`
- repo-wide `npm run typecheck` still has unrelated pre-existing failures outside the main household-expenses workflows

That means the minimum serious test bar is:

- live browser behavior
- query/mutation success
- SQLite state confirmation

## Standard Method

### 1. Identify which layer changed

Before testing, determine whether the change touched:

- Python ingest/categorization in [`backend/src`](./backend/src)
- tRPC/database logic in [`frontend/src/server`](./frontend/src/server)
- operator UI in [`frontend/src/app`](./frontend/src/app)

That tells you what must be verified end to end.

### 2. Use the running UI for user-facing work

Typical local path:

```bash
cd frontend
npm run dev -- --port 3010
```

Primary operator routes:

- `/upload`
- `/validation`
- `/review`
- `/fixer`
- `/export`
- `/`

### 3. Use an isolated DB copy when the test is risky

Do this for:

- destructive repair work
- bulk deletes
- migration/backfill logic
- import cleanup experiments
- anything that might damage trusted historical labels

### 4. Verify persisted effects directly

For any workflow that writes important state, inspect the actual rows after the action.

Fields that commonly need explicit verification:

- `is_approved`
- `approved_at`
- `needs_validation`
- `category_id`
- `subtype_id`
- `merchant`
- `original_input_name`
- `split`
- `member_a_spend`
- `member_b_spend`
- `tag`
- `log_to_work`
- `track_with_friends`

## Playbooks

### Upload and ingest

Use this when changing upload UI, file parsing, or source-card handling.

1. Upload a real sample file through `/upload`.
2. Confirm the result message reports:
   - processed rows
   - duplicates skipped
   - payments/credits skipped
   - errors, if any
3. Confirm the selected statement source freshness updates.
4. Confirm new rows land in SQLite with the expected `card`, `spender`, and source-file metadata.
5. Confirm non-canonical imports land staged, not silently approved.

### Validation and approval

Use this for [`frontend/src/app/validation/page.tsx`](./frontend/src/app/validation/page.tsx) and [`frontend/src/server/api/routers/validation.ts`](./frontend/src/server/api/routers/validation.ts).

1. Open `/validation`.
2. Pick a staged transaction.
3. Set:
   - category
   - subtype
   - canonical merchant
   - split
   - tag
   - work logging
   - friend allocation if relevant
4. Save approval.
5. Confirm the row leaves staging.
6. Confirm the row now has:
   - `is_approved = 1`
   - a real `approved_at`
   - coherent allocation fields
7. If `Remember mapping` was enabled, confirm the learned merchant mapping row was written correctly against the original imported merchant text.

### Friend-sharing allocation

Use this when changing split, friend sharing, or allocation math.

1. Open a staged item in `/validation`.
2. Add one or more friend shares.
3. Test both modes:
   - `Percentages`
   - `Exact amounts`
4. Confirm save is blocked until the transaction is fully allocated.
5. Approve the item.
6. Verify:
   - `expense_friend_shares` rows exist
   - `track_with_friends` reflects the selected friends
   - `member_a_spend` / `member_b_spend` reflect the household remainder after friend shares

### Review and reflect

Use this for [`frontend/src/app/review/page.tsx`](./frontend/src/app/review/page.tsx) and [`frontend/src/server/api/routers/expenses.ts`](./frontend/src/server/api/routers/expenses.ts).

1. Open `/review`.
2. Confirm the three tabs exist:
   - `Spending`
   - `Work`
   - `Tags`
3. In `Spending`, confirm:
   - category lensing works
   - category click-through drills into subtype
   - trends use expense date
   - settlement numbers match DB expectations
4. In `Work`, confirm only `log_to_work = true` expenses appear.
5. In `Tags`, confirm:
   - tag list only includes validated tagged expenses
   - selecting one tag changes the trend and category mix
   - the tab remains lighter than the full spending dashboard

### Settlement and payments

1. Open the `Spending` tab in `/review`.
2. Log a payment between the two household members.
3. Confirm the payment appears in the payment list.
4. Confirm the settlement numbers update.
5. Verify the `payments` table directly.

### Table Fixer

Use this for [`frontend/src/app/fixer/page.tsx`](./frontend/src/app/fixer/page.tsx).

1. Open `/fixer`.
2. Filter to a known row.
3. Expand the row inline.
4. Edit one or more fields.
5. Save.
6. Confirm the DB row changed as expected.
7. Test single delete and bulk delete only against an isolated DB when possible.
8. Confirm linked validation queue rows are cleaned up on delete.

### Export

1. Open `/export`.
2. Choose a range.
3. Download the CSV.
4. Confirm the header matches the original household export shape.
5. Spot-check several rows against the database.

## What To Trust

Trust most:

- persisted SQLite state
- live operator workflow behavior
- direct route/query responses

Trust less:

- generic compile cleanliness across the whole repo
- assumptions based only on UI appearance

## Minimum Bar Before Saying “Ready”

A change is ready to hand back only if:

- the relevant route loads
- the intended workflow actually runs
- the persisted state is coherent
- any destructive side effects were controlled
- any remaining known gaps are stated explicitly
