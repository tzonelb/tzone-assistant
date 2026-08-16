# Roadmap

The point of the module architecture is that this list is a list of *directories to add*, not of
rewrites. Everything below is additive.

## Built

| Module | Both sides | What it gives you |
|---|---|---|
| `base` | ✅ | Sign-in, devices, company settings, the shell, the module browser |
| `accounting` | ✅ | Chart of accounts, double-entry journal, posting engine, void/reversal |
| `partners` | ✅ | Customers and suppliers with control accounts |
| `catalog` | ✅ | Products and services with default prices, tax and accounts |
| `documents` | ✅ | The pluggable document layer: storage, numbering, aging, type registry |
| `invoicing` | ✅ | Sales and purchase invoices with per-line tax |
| `payments` | ✅ | Receipts and payments with allocation against open invoices |
| `reports` | ✅ | Trial balance, P&L, balance sheet, general ledger, aging, cash position |
| `dashboard` | frontend | Front page that renders cards contributed by other modules |
| `audit_log` | backend | Server-side record of every replicated change and every rejection |

Plus the kernel on both sides: module loading, generic replication, the outbox sync engine, the
PWA shell, integer money, and Arabic/English with RTL.

## Next — high value, small modules

| Module | Depends on | Notes |
|---|---|---|
| `opening_balances` | `accounting` | Import a trial balance; difference to opening-balance equity |
| `credit_notes` | `documents`, `invoicing` | One `DocumentType` with `role='settlement'` — joins aging for free |
| `printing` | `documents` | Print/PDF templates per document type, contributed via a hook |
| `multicurrency` | `accounting` | Rate table, revaluation entry, FX gain/loss accounts |
| `inventory` | `catalog`, `invoicing` | Stock moves, valuation, COGS posting on sale |
| `expenses` | `accounting`, `partners` | Employee claims, approval, posting on approve |
| `payroll` | `accounting`, `partners` | Salary runs as a document type |
| `fixed_assets` | `accounting` | Asset register and scheduled depreciation entries |
| `tax_returns` | `accounting`, `documents` | Period VAT return from the tax accounts |
| `budgets` | `accounting`, `reports` | Budget vs actual as a report provider |
| `pos` | `invoicing`, `payments` | Touch screen that posts an invoice plus a receipt in one step |
| `bank_import` | `accounting` | Statement import and reconciliation against cash accounts |

## Kernel work

- **Roles and permissions.** Per-module permission declarations, enforced on both sides. The hook
  bus is the natural seam; the entity descriptor is the natural place to declare who may write.
- **Attachments.** Binary storage with its own sync path — deliberately not the JSON one.
- **Local database encryption.** Passphrase-derived key over the Dexie tables; see
  [OFFLINE_SYNC.md §8](OFFLINE_SYNC.md#8-what-is-deliberately-not-built) for why it is not there today.
- **Multi-company.** A company id on every record, and a company selector; the replication
  envelope is the right place to add it.
- **Server-side module install/uninstall at runtime**, rather than by environment variable.

## Explicitly out of scope for now

- CRDT merge of concurrent edits. Posted documents are immutable, so last-writer-wins on the
  small mutable tables is sufficient — see [OFFLINE_SYNC.md §5](OFFLINE_SYNC.md#5-why-conflicts-are-rare-here).
- Caching API responses offline. IndexedDB is the offline source of truth; a cached response
  would compete with it.
