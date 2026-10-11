# Tuner -- Design Tokens

Every color, radius and shadow in the page comes from this sheet. Copy these values verbatim into the `<style>` block. Never write a raw color, radius, or shadow anywhere else in the app.

## Color tokens

```css
:root{
  --bg:#f4f6fb; --card:#ffffff; --border:#e3e8f0; --text:#1a2233;
  --muted:#6b7688; --accent:#2563eb; --track:#e9edf5;
  --green:#16a34a; --greenbg:#dcfce7; --red:#dc2626; --redbg:#fee2e2;
  --amber:#d97706; --amberbg:#fef3c7; --shadow:0 1px 3px rgba(20,30,60,.06);
  --on-accent:#ffffff;
}
@media (prefers-color-scheme:dark){
  :root{
    --bg:#0f141c; --card:#171e29; --border:#26303f; --text:#e7ecf3;
    --muted:#8a97ab; --accent:#4f86f7; --track:#232d3c;
    --green:#22c55e; --greenbg:#0f2f1c; --red:#ef4444; --redbg:#331515;
    --amber:#f59e0b; --amberbg:#33270c; --shadow:0 1px 3px rgba(0,0,0,.3);
    --on-accent:#ffffff;
  }
}
```

## Semantic use

| Token | Used for |
| --- | --- |
| `--accent` | Primary buttons, active progress fill, links, focused inputs |
| `--green` / `--greenbg` | Downloaded, enabled channel, connection test passed |
| `--amber` / `--amberbg` | Queued, deferred for free space, skipped by a filter |
| `--red` / `--redbg` | Failed download, missing file, disabled channel, delete actions |
| `--muted` | Secondary text, timestamps, counts, filter summaries |
| `--track` | Progress bar and disk bar background |

Status colors are load bearing. A row's state is readable from color plus its text label, never from color alone.

## Type and layout

- Font stack: `-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Ubuntu,sans-serif`. Base 15px, line-height 1.45.
- Page: `.wrap` at `max-width:960px`, `margin:0 auto`, `padding:14px 16px 60px`.
- Section headings: 13px, uppercase, `letter-spacing:.06em`, color `--muted`, weight 700.
- Card: `background:var(--card)`, `border:1px solid var(--border)`, `border-radius:14px`, `padding:15px 16px`, `box-shadow:var(--shadow)`.
- Chip: `border-radius:999px`, `padding:4px 11px`, `font-size:12.5px`, color `--muted`, bold values in `--text`.
- Buttons: `border-radius:10px`, `padding:7px 11px`, `font-size:13px`, weight 600, `:active{transform:translateY(1px)}`.
- Grid: `repeat(auto-fill,minmax(280px,1fr))` with `gap:12px`.
- Monospace only for file paths and yt-dlp output: `ui-monospace,SFMono-Regular,Menlo,monospace` at 12px.

## The channel list

Channels render as rows in one panel, never as one card each. A card grid
reads fine at four channels and becomes unusable at forty, and the operator
is expected to accumulate them.

- Panel: `.list`, same surface, border, radius and shadow as `.card`, with
  `overflow:hidden` so the rows clip to the corners.
- Row: two lines. Name at 14.5px weight 650, then a meta line at 12px in
  `--muted` reading `<n> videos · <size> · <last checked>`. Both truncate
  with an ellipsis.
- Leading dot at 8px: `--green` enabled, `--muted` paused, `--red` when the
  channel carries an error.
- Trailing pills appear **only** for states needing action (failed, missing,
  deferred, queued, downloading). A healthy channel shows none, so the
  colour in the list means something.
- The whole row is a `<button>` carrying `aria-expanded` and an
  `aria-label` of the channel name. It opens in place; controls live in the
  body rather than on every row at once.
- Under 560px the row header wraps: the name takes the first line and the
  pills drop below it. The name is how a channel is identified and must not
  be squeezed into an ellipsis by its own status.

## Two rules learned the hard way

- **Never name a modifier class `wrap`.** `.row.wrap` also matches `.wrap`,
  the page container, and silently inherits its `padding:14px 16px 60px`.
  The flex modifier is `.row.wrapped`.
- **Flex items holding truncating text use `flex:1 1 0`, not `1 1 auto`.**
  With `auto` the item claims its max-content width while the browser
  decides where to wrap, so one long channel name pushes its neighbours to
  another line instead of ellipsising.

## Redrawing under a live poll

The page re-renders every 2 seconds, so any panel holding controls must
refuse to redraw while the user is mid-edit. That guard checks for
`INPUT`, `SELECT` or `TEXTAREA` only. Including `BUTTON` deadlocks the
panel: clicking a button focuses it, and the panel then refuses to render
the result of the click.

## Behavior

- Mobile first. Everything reflows to one column under 560px; touch targets stay at 40px minimum height.
- Light and dark follow `prefers-color-scheme` only. No theme toggle, no stored preference.
- Progress bars animate width transitions at 200ms linear and nothing else. No spinners, no skeletons, no motion beyond that.
- The page polls `/api/state` on a 2 second interval and re-renders from that single payload. No partial DOM updates from action responses.
