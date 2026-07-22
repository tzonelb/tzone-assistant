# Data and Security

## Data classes

### Public

- product documentation approved for publication;
- non-sensitive repository docs;
- public marketing content.

### Internal

- architecture;
- workflows;
- roadmap;
- internal operational reports.

### Confidential

- customer names, phone numbers, IDs, conversations, notes, orders, repairs, and payments;
- employee data;
- branch financial data;
- private knowledge base items.

### Secret

- API keys and access tokens;
- passwords;
- JWT secrets;
- private keys;
- database credentials;
- webhook verify secrets.

## Required controls

- Backend tenant scoping.
- Role-based authorization.
- Audit logging for sensitive changes.
- Encryption in transit.
- Secure secret storage.
- Backup access controls.
- Data retention and deletion policy.
- Production database access logging.
- Principle of least privilege.

## Public repository rule

A public repository may contain source code and safe documentation only. It must never contain customer data, production database files, logs with personal data, or secrets.

## Future compliance work

Before scaling to multiple companies or external customers:

- formal privacy policy;
- data-processing agreements where required;
- retention/deletion controls;
- export/delete customer data tools;
- incident response process;
- security review and penetration testing.
