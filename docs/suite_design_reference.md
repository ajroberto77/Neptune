# Iridium Suite — Design Reference (CATO / Mercury / Iridium Backend)

> Captured from the Iridium Backend Electron app (forked from Mercury, the shell CATO also
> uses) so Neptune's styling decisions can be made against the rest of the desktop suite even
> though those repos aren't checked out here. This is a **reference**, not a mandate — see
> `.claude/agents/gui-designer.md` for how it's used. The headline fact: **the suite is
> light-themed; Neptune is currently dark ("Deep Ocean").** Fonts already match.

## Theme & palette (light)

The suite uses a light, "institutional finance" palette defined as CSS variables in
`global.css`, with `color-scheme: light`:

| Token | Value | Role |
|-------|-------|------|
| `--bg0 … --bg4` | `#FAFBFC` → `#D1D8E0` | surface ramp (near-white → light steel) |
| `--border`, `--border2` | `#D1D8E0`, `#A8C5DD` | hairline / emphasized borders |
| `--navy`, `--blue`, `--steel` | `#0F2C47`, `#1B4B78`, `#4A7BA7` | primary brand blues |
| `--gold`, `--gold-dim` | `#C9A961`, `#9B7E4F` | accent / highlight (the "Iridium gold") |
| `--teal`, `--red` | `#2E7D52`, `#C0392B` | positive / negative (P&L, OK/error) |
| `--violet`, `--orange`, `--amber` | `#8b7cf8`, `#e8874a`, `#D49547` | categorical accents |
| `--text0 … --text3` | `#1A2B3C` → `#405e7a` | text ramp (dark navy → steel) |
| shadows | `--shadow-sm/md` | soft navy-tinted elevation |

## Typography

- **Body / UI:** `Inter` (`--sans`). **Display / labels:** `Outfit` (`--display`).
- Small, dense type: badges ~9px, buttons 11–12px, body 12.5–13px, section labels 9.5px
  uppercase with wide letter-spacing (`0.07–0.16em`).
- **Neptune already uses Inter + Outfit** (see `frontend/tailwind.config.js`), so typography
  is the one axis already aligned.

## Reusable component primitives (all in `global.css`)

- **Badges `.bdg`** + color variants (`-teal/-red/-violet/-gold/-blue/-muted/-orange`):
  uppercase, 9px/700, 1px border, tinted background. The suite's status-pill vocabulary.
- **Buttons `.btn`** + `-primary` (solid blue), `-secondary` (light), `-ghost`, `-danger`
  (red-tinted), `-sm`. 12px/500, 5px radius, `transition: all .12s`, `:disabled` → 0.45 opacity.
- **Notices `.notice`** + `-gold/-info/-blue/-violet/-error`: tinted callout panels.
- **Section divider `.dash-row`**: an uppercase display-font label + a hairline rule —
  the suite's way of titling a band of content.
- **Scrollbars:** ultra-thin (5px), transparent track, `--border2` thumb.
- **Animations:** `pulse` (running/live dot), `spin` (loaders), `indeterminate-slide`
  (progress bars).

## App shell & interaction patterns

- **Layout:** full-viewport flex column, `overflow: hidden`; a **custom `TitleBar`** +
  draggable **`TabBar`** + a single active tab panel. `-webkit-app-region` is used (frameless
  window with an app-drawn title bar), and `user-select: none` for a native-app feel.
- **Tabs:** reorderable, order persisted in `localStorage`; a small pulsing **"running dot"**
  marks tabs with active background work; per-tab remount keys for refresh.
- **Settings:** a modal `SettingsPanel` driven by the Electron config (DB connections +
  provider keys), opened from the title bar.
- **Backend status:** a health pill in the title bar fed by `/health`, plus a docked log/terminal
  area streamed over the IPC bridge (`onApiLog`).

## How Neptune differs today

- **Dark** "Deep Ocean" Tailwind theme (`ocean.bg #0a0e1a`, `ocean.panel #111726`,
  `ocean.accent #3b82f6`; `status.ok/watch/breach` green/amber/red) vs the suite's light palette.
- **Tailwind utility classes** + a couple of `@layer components` helpers (`.np-input`) vs the
  suite's hand-rolled BEM-ish CSS component system (`.btn`, `.bdg`, `.notice`, `.dash-row`).
- **Standard browser/window chrome** (no custom TitleBar/TabBar, no app-region drag, no running
  dots, no docked log) vs the suite's bespoke shell.
- Status semantics are richer in Neptune (OK/WATCH/BREACH for factor/beta limits) and must be
  preserved by any restyle.
