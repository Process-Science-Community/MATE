# Landing page

The public marketing page for [MATE](https://mate.uni-muenster.de), served
from GitHub Pages. One dependency-free `index.html` plus `assets/` and the
static documentation under `docs/` (the Docs tab) - no build step, no framework,
no tracking.

Deployed by [`.github/workflows/pages.yml`](../.github/workflows/pages.yml) on
every push to `main` that touches this directory. Edit `index.html`, push, done.

The documentation under `docs/site/` is generated - edit the markdown under
`docs/content/` and run `make docs` (`node landing/docs/build.mjs`), then commit
the regenerated pages. `docs/build.mjs` keeps the landing page's design system
(tokens, background, top bar, buttons, cards, footer) and layers on the docs
layout: a rail that carries the whole hierarchy (group, chapter, and the current
chapter's sections), a pager, and Ctrl+K search. Change `index.html`, change
`build.mjs`.

To preview locally:

```bash
python3 -m http.server -d landing 8081
```

## Cursor glow

The page background follows the pointer: three soft colour fields in a fixed
layer behind everything (`.cursor-glow`), each easing toward its own offset of
the pointer at a different rate. A few properties keep it cheap and calm:

- The fields move with `transform` only, so a frame is a compositor update and
  never a repaint of a 78vmax gradient.
- Pointer events only set a target; an exponential filter (frame-rate
  independent, `dt`-compensated) does the easing, so motion never snaps.
- The loop stops as soon as every field has settled, and is skipped entirely for
  touch pointers and `prefers-reduced-motion: reduce`.
- It fades in on the first pointer move and fades out while the pointer is
  outside the window.

Because the layer sits *behind* the page (`z-index: -1`), a full-width section
with an opaque background would hide it. The bands that span the page therefore
use `color-mix(in srgb, var(--surface) 72%, transparent)` (or `--panel` at 76%).
Keep that in mind when adding a new full-width section.

## Screenshots

`index.html` expects six PNGs in `assets/`. Any that are missing fall back to
`assets/placeholder.svg` ("Screenshot pending"), so the page never shows a
broken image - drop the real file in and it takes over on the next deploy.

| File | Where it appears | What to capture |
| --- | --- | --- |
| `shot-hero.png` | Hero, full width | A process overview with the module library visible and the MATE AI sidebar open - the one shot that has to sell the product at a glance. |
| `shot-processes.png` | Tab 01, Workspace | The processes list with several imported logs, at least one of them object-centric (OCEL), so the format badges show. |
| `shot-modules.png` | Tab 02, Module library | One process's module library, scrolled so the category grouping (discovery / intelligence / comparison) is readable. |
| `shot-crossmodule.png` | Tab 03, Cross-module | Performance over Time on a log with a CV4CDD-detected drift: the shaded drift band must be visible in the chart, so one module's result shows up inside another's view. |
| `shot-dashboard.png` | Tab 04, Dashboards | A dashboard with a mix of widget kinds on one canvas - KPI tiles, a chart, and a model view. |
| `shot-mate-ai.png` | Tab 05, MATE AI | The MATE AI sidebar mid-conversation next to a process page, with an answer that shows it reasoning about the process. |

Capture notes:

- **Size**: a 1440x1020 browser viewport at 2x (so 2880x2040 PNG). The theater
  frame crops to a 24:17 aspect and anchors to the top, so a little extra height
  is fine but extra width gets cut.
- **Chrome**: content only, no browser UI, no OS window shadow. Light theme.
- **Data**: this page is public. Use demo logs - no real client data, no real
  user names, and check the account menu and any "shared with" avatars before
  shipping a shot.
- **Weight**: run them through an optimizer (`oxipng -o4`, `pngquant`) and aim
  for well under 1 MB each; the hero is the first thing that loads.
