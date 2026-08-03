# T-ZONE — Project rules for Claude Code

## Theme: use it, never regenerate it

This project has a fixed visual system. It lives in `src/styles/` (or wherever you copied `theme/`):

- `classical-styles.css` — the Classical design system: all tokens + components.
- `tzone-theme.css` — the T-ZONE layer: brand blue accent, `tz-*` classes.

**Hard rules — no exceptions:**

1. **Never rewrite, regenerate, restyle, "improve", or replace these two files.** Load them as-is, in this order: `classical-styles.css`, then `tzone-theme.css`.
2. **Never write a hex color.** Every color comes from `var(--color-*)`.
3. **Never write a raw px value for spacing or radius.** Use `var(--space-*)` and `var(--radius-*)`.
4. **Never introduce a new font.** Cormorant Garamond for headings, Lora for body — already loaded.
5. **Never add a new CSS framework, Tailwind config, or component library** for styling. Compose with the existing classes: `.btn` (`.btn-primary` / `.btn-secondary` / `.btn-ghost` / `.btn-block`), `.tag`, `.card`, `.table`, `.input`, `.field`, `.seg`, `.hr`, `.plate`.
6. **Buttons and cards are outlined, not filled.** No solid accent fills. No heavy shadows — `var(--shadow-sm/md/lg)` only.
7. **Icons: Lucide only**, stroke-width 1.5–1.6. Don't hand-draw SVG icons.
8. **Numbers use tabular figures** — class `tz-num` or `tz-fig`.
9. **Focus ring:** `outline: 2px solid var(--color-accent); outline-offset: 2px`. Never the browser default.
10. **Logo:** use `assets/tzone-logo-transparent.png`. Do not draw or recreate the mark.

If a design need isn't covered by an existing token or class, **ask before inventing one**.

## Design references

`design_handoff_tzone_theme/reference/` holds the approved HTML mockups (desktop platform + mobile app). They are hi-fi: colors, type, and spacing are final. Recreate them in this codebase's own framework and patterns — do not ship the HTML directly, and do not deviate from their layout or values.

## Mobile

Touch targets minimum 44px. Bottom tab bar respects `env(safe-area-inset-bottom)`. Works on both iOS and Android — no platform-specific styling.
