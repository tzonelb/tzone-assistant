# Conversation State Machine

## Canonical states

### `AI_ACTIVE`

- No employee owner.
- AI may reply according to policy.
- Employees may view.
- An authorized employee can atomically take control.

### `HUMAN_QUEUE`

- AI is stopped.
- No owner.
- Conversation waits for an employee.

### `HUMAN_OWNED`

- Exactly one owner.
- AI is stopped.
- Owner can reply and use allowed controls.
- Other normal employees are read-only.
- Admin can override, transfer, release, or return to AI.

### `DONE`

- Conversation completed.
- New customer activity may reopen it according to policy.

### `ARCHIVED`

- Removed from the active inbox.
- Remains searchable and auditable.

## Required transitions

```text
AI_ACTIVE --human takeover--> HUMAN_OWNED
AI_ACTIVE --escalation------> HUMAN_QUEUE
HUMAN_QUEUE --takeover------> HUMAN_OWNED
HUMAN_OWNED --release-------> HUMAN_QUEUE
HUMAN_OWNED --return to AI--> AI_ACTIVE
HUMAN_OWNED --complete------> DONE
DONE --new customer message-> AI_ACTIVE or HUMAN_QUEUE
Any allowed state --archive-> ARCHIVED
```

## Ownership contract

- The takeover operation is an atomic compare-and-set transaction.
- A stale second takeover returns HTTP 409.
- The current owner remains unchanged after conflict.
- Reply and heartbeat renew the lease.
- A late AI completion cannot remove or override a human owner.
- Transfer is atomic.
- Release clears the owner and keeps AI disabled.
- Return to AI clears the owner and enables AI.

## Permissions

### Owner

- reply;
- read/unread action;
- release;
- permitted status/priority/department actions;
- add notes/tags according to role.

### Normal non-owner

- view only;
- no reply;
- no takeover when an active owner exists;
- no release/return-to-AI/transfer/read-state control.

### Administrator

- explicit override;
- transfer;
- release;
- return to AI;
- read-state and workflow control.

## Timeline events

- customer message;
- AI reply;
- employee open;
- ownership/takeover/override;
- release/transfer;
- employee reply;
- read/unread;
- return to AI;
- status/priority/department/tag change;
- note creation.

Timeline and notifications are separate concepts. AI replies belong in Timeline and do not create bell notifications.
