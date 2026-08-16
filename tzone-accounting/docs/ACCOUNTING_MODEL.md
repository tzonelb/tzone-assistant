# Accounting model

## 1. Money representation

Every monetary value in this system is an **integer in minor units** of its currency
(`1234` = 12.34 USD, `1500` = 1500 IQD because IQD is configured with 0 decimals).

No monetary value is ever stored, transmitted, or summed as a floating point number. Conversion
to a display string happens once, at the edge, in `formatMoney()`.

A currency is defined by `{ code, decimals, symbol }`. The company has one **base currency**;
every journal line carries both its transaction amount and the base-currency amount derived from
the document's FX rate, so reports never need historical rate lookups.

## 2. Entities

### Account
| Field | Notes |
|---|---|
| `id` | UUID, generated on the device that created it |
| `code` | e.g. `1100`, unique, used for ordering and lookup |
| `name_en`, `name_ar` | both required |
| `type` | `asset` \| `liability` \| `equity` \| `income` \| `expense` |
| `parent_id` | nullable, builds the tree |
| `is_group` | group accounts cannot be posted to |
| `currency` | nullable; when set, only that currency may be posted |
| `is_active` | soft disable |

The **normal balance** follows the type: `asset` and `expense` are debit-normal, the rest are
credit-normal. Report signs are derived from this, never hardcoded per account.

### Partner
Customers and suppliers share one table with `kind: customer | supplier | both`. Each partner
resolves to a **control account**: `receivable_account_id` for sales, `payable_account_id` for
purchases, defaulting to the company-level control accounts in settings.

### Item
A product or service with a default `income_account_id` (used on sales) and
`expense_account_id` (used on purchases), a default unit price, and a default tax rate.

### Journal entry
The only thing that ever touches the ledger.

```
JournalEntry {
  id, entry_no, date, memo, currency, fx_rate,
  status: draft | posted | void,
  source: { kind: manual | sales_invoice | purchase_invoice | receipt | payment | opening,
            id },
  lines: JournalLine[]
}

JournalLine {
  account_id, partner_id?, description,
  debit, credit,                 // transaction currency, minor units
  base_debit, base_credit        // base currency, minor units
}
```

Invariants enforced on **post** (client and server both check):

1. `sum(debit) == sum(credit)` and `sum(base_debit) == sum(base_credit)`
2. Every line has exactly one non-zero side — a line is either a debit or a credit
3. At least two lines
4. No line targets a group account or an inactive account
5. `date` is not before the settings lock date
6. Posted entries are **immutable**. A mistake is corrected by voiding, which writes a mirrored
   reversing entry dated on the void date; the original stays in the audit trail.

### Documents

`SalesInvoice`, `PurchaseInvoice`, `Receipt`, `Payment` are *source documents*. They hold the
commercial detail (lines, tax, due date, allocations) and, once posted, own exactly one journal
entry via `journal_entry_id`. Reports never read documents — they read the journal. Documents
exist for workflow, printing and aging.

## 3. Posting rules

All amounts below are in the document currency; the base-currency mirror is computed with the
document FX rate.

### Sales invoice
```
Dr  Accounts receivable (partner control)      total
    Cr  Income account (per line)                        line net
    Cr  Tax payable                                      tax total
```

### Purchase invoice
```
Dr  Expense / inventory account (per line)     line net
Dr  Tax receivable                             tax total
    Cr  Accounts payable (partner control)               total
```

### Receipt (money in from a customer)
```
Dr  Cash / bank account                        amount
    Cr  Accounts receivable (partner control)            amount
```

### Payment (money out to a supplier)
```
Dr  Accounts payable (partner control)         amount
    Cr  Cash / bank account                              amount
```

### Opening balances
A single `opening` entry per fiscal start, with the difference posted to
`Opening balance equity`, so the entry balances even when the imported trial balance does not.

Allocation of a receipt/payment against specific invoices affects **aging only** — it does not
create additional journal lines. The ledger already reflects the cash movement.

## 4. Reports

Every report is a pure function over posted journal lines. Signatures live in
`frontend/src/modules/reports/calculators.ts` and are mirrored by SQL in
`backend/app/modules/reports/calculators.py`. Aging is the exception: it is computed generically
over document types in `frontend/src/modules/reports/calculators.ts` and
`backend/app/modules/documents/aging.py`, so a document type contributed by any module joins it
without either side changing.

| Report | Definition |
|---|---|
| **Trial balance** | Per account: `Σ base_debit`, `Σ base_credit`, and the net on the account's normal side, for `date ∈ [from, to]`. The two totals must be equal. |
| **General ledger** | Per account: opening balance before `from`, then each line in `[from, to]` with a running balance. |
| **Profit & loss** | Income accounts (credit-normal net) minus expense accounts (debit-normal net) over the period. |
| **Balance sheet** | Assets = Liabilities + Equity, where equity includes retained earnings = cumulative P&L up to `as_of`. Cumulative from the beginning of time to `as_of`. |
| **AR / AP aging** | Per partner and per open invoice: `outstanding = total − allocated`, bucketed by days past `due_date` into `current / 1–30 / 31–60 / 61–90 / 90+`. |
| **Partner statement** | Ledger movement of the partner's control account filtered by `partner_id`, with a running balance. |
| **Cash position** | Balance of every account flagged `is_cash` as of a date, plus the period's inflow and outflow. |

Balance sheet and trial balance are asserted to balance in the test suite; an unbalanced result
is a bug, not a rounding artefact, because all arithmetic is integer.

## 5. Validation split

The client performs the full document → journal mapping, because it must work with no network.
The server does **not** re-derive that mapping; it independently re-validates the invariants in
§2 before storing anything, and rejects the offending record otherwise. This keeps one
implementation of the business rules while still refusing to persist a corrupt ledger from a
tampered or buggy client.

A rejected record is reported back with its reason and kept in the client's outbox, flagged, and
shown on the settings screen. It is never silently dropped: a rejection means the two ledgers
disagree, and someone has to decide which one is right.
