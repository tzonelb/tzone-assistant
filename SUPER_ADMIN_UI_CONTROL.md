# T-ZONE MASTER UI LOCK

This package keeps the customer-facing visual identity fixed and adds a configuration foundation for the future Super Admin control plane.

## Locked customer UI
- T-ZONE logo and brand colours remain unchanged.
- Dashboard keeps normal page scrolling.
- Conversations and Comments use viewport-height workspaces with internal scrolling only.
- Chat composer remains a single row: attachments, image, voice, wide input, circular send button.
- Pinned conversations and the channel pin-bar are removed; Starred remains.

## Super Admin configuration foundation
The customer frontend now reads its navigation, branding, module visibility and layout rules from:

`GET /api/platform-ui/config`

Until that backend endpoint exists, the interface uses:

`frontend/src/config/platformDefaults.js`

The future Super Admin portal can safely control:
- Menu labels, order and visibility.
- Module activation per tenant.
- Brand name and theme tokens.
- Appointments and optional modules.
- Customer UI layout flags.

The Super Admin portal itself should remain a separate application/control plane and must not reuse the customer dashboard permissions.
