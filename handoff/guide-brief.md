# Brief: write a MATE module's HOW_TO_USE.md

You are writing end-user documentation for modules of MATE, a process-mining web platform.
Work ONLY inside this git worktree (it is your cwd):

    /Users/yannickbrusa/Documents/MATE/.claude/worktrees/module-how-to-use-guides

Hard rules for the environment:
- Never `cd` out of it, and never read or write the main checkout `/Users/yannickbrusa/Documents/MATE/...` outside `.claude/worktrees/`.
- No git commands. Do not start servers, do not run the full test suite, do not install anything.
- Edit only the files you are assigned (see "Rewrites"). Do not edit manifests, panels, code, tests or docs.
- Shell commands in this sandbox are fragile: use literal paths, no shell variables, no heredocs piped into `uv`, no `cd`.

## What to produce

For each module you are assigned, create `modules/<id>/HOW_TO_USE.md` (the `<id>` is the folder name).
Then check it:

    uv run --frozen python -m mate.sdk.guide modules/<id>

It must print `ok   <id>`. (Several: `uv run --frozen python -m mate.sdk.guide modules/a modules/b`.)
Fix every reported problem; the checker lists them all with line numbers.

## Read these first

1. `modules/HOW_TO_USE.template.md` - the skeleton.
2. `modules/temporal_mapper/HOW_TO_USE.md` - the reference example. Match its depth, tone and structure:
   Settings split into `###` tables when there are two kinds of controls; "Reading the results" with one
   `###` per view/chart/table in the order the panel shows them; plain prose; no marketing words.
3. The module itself (see "Accuracy protocol").

## The fixed format (the checker enforces all of this)

```
# How to use <manifest name, exactly>      <- the module's `name:` in manifest.yaml, e.g. "# How to use Conformance Checking"

<Lead: ONE paragraph, 1-3 sentences, at most 600 characters.>

## When to use it
## Before you start
## Settings
## Reading the results
## Tips and limits
## Method
```

- Exactly these six `##` sections, in this order, none empty, no others. `###` and `####` are allowed inside; nothing deeper.
- `## Settings`: every table must have exactly the header `| Setting | Default | What it changes |`.
  If the module has no user-adjustable controls at all, write only the sentence `This module has no settings.`
- Links: only absolute `https://` / `http://` / `mailto:` (a repo file link is `https://github.com/Process-Science-Community/MATE/blob/main/<path>`).
- Not allowed anywhere outside code: images, raw HTML or angle brackets (write `<module_id>` inside backticks; there is no `<kbd>`), HTML comments, nested/indented lists, horizontal rules (`---`), task lists, footnotes, reference-style links, strikethrough, `~~~` fences. Use `**bold**`, `*italic*`, `` `code` ``, GFM pipe tables, flat `-` / `1.` lists, ``` fences, and callouts written as `> [!NOTE]`, `> [!TIP]` or `> [!WARNING]` on the first line of a block quote.
- UTF-8, at most 64 KiB (aim for 70-160 lines).

## What each section holds

- **Lead**: the question the module answers and what you get back.
- **When to use it**: 2-4 bullets: situations it fits, and when another MATE module is the better tool (name that module).
- **Before you start**: the log model and required columns and minimum size (`requirements.event_log` in the manifest), other modules it depends on (`requirements.modules`), and anything external: an uploaded model or file, a second log, an API key, a sidecar service, a JRE. Say how long it takes / whether it computes on open (see "Lazy compute" below) only if the code shows it.
- **Settings**: every control a user can change, with its exact on-screen label (bold), default and range, and what it changes. Two sources: controls in the panel, and configuration on the module page (`config_schema` in the manifest; the module detail page shows a form for it). Use `### In the panel` / `### On the module page` when both exist.
- **Reading the results**: one `###` per view, chart, table or tab, in the panel's order: what it shows, how to read it, what to look for, what is clickable. End with `### Dashboard cards` if the manifest declares `frontend.widgets`: one line per card (its title, what it shows; from the widget `help` and the widget source).
- **Tips and limits**: sizes/limits, run time, typical pitfalls, what the module does not do, and which other module to use next.
- **Method**: how the result is computed, in a few sentences to two short paragraphs. Name the method and its authors/year in prose. NO reference list and no "References" heading: formal citations live in the manifest's `source` and appear in the info popover.

## Accuracy protocol (the most important part)

Every statement about the UI or behaviour must be verified in code before you write it. If you cannot verify something, leave it out and list it under "Unverified/omitted" in your report. Typical failure classes to avoid (the maintainer made all three while drafting the reference guide from memory and had to correct them):
1. What a control does to results that are already on screen (e.g. claiming a change "clears the map" when the code only hides one panel). Read the handler.
2. Which entity, period, unit or direction a number is attached to. Read the computation.
3. Invented names, years, numbers or limits. Never write an author's first name, a year or a number that is not in the manifest `source`, the README or the code. Cite authors by surname as written there.

Sources, in priority order:
- `modules/<id>/panel/**/*.tsx`: exact labels, defaults, ranges, what happens on click/change, empty/error states, tab names.
- `modules/<id>/manifest.yaml`: `name`, `description`, `about`, `requirements` (log model, columns, `min_events`, `min_cases`, `modules`), `config_schema` (keys, labels, defaults, ranges), `frontend.widgets` (titles, `help`), `precompute`, `dependencies`.
- `modules/<id>/module.py` and its core files: routes, limits, algorithms, error messages, defaults.
- Widgets: `modules/<id>/widgets/*.tsx`. Existing `README.md` and `tests/`.
- Shared UI the module reuses (canvas toolbars, filter bar, run gate) is documented where the module uses it: read `apps/web/components/visualizations/canvases/shared/canvas-toolbar.tsx`, `apps/web/components/processes/log-filter.tsx` and `apps/web/components/processes/module-run-gate.tsx` only to learn exact labels.
- Do not describe developer detail (file names, API routes, class names) unless a user needs it, e.g. which file types an upload accepts.
- Do not describe anything the panel does not actually expose.

**Lazy compute.** Many modules compute when you open their page: MATE may show a progress bar with a Cancel button before the panel appears. Mention it only if the manifest has `precompute` or `requirements.modules` and the run gate applies, using the wording the run gate shows.

**Shared log filter.** Module pages show a filter bar above the panel when the manifest does not set `frontend.log_filter: false`; one sentence in "Tips and limits" is enough if you verify it in `log-filter.tsx`.

## Rewrites (only the modules your prompt marks as REWRITE)

These modules already have user-facing text in their `README.md` (or in the panel). Move what users need into the guide, then trim the README:
- Remove from `README.md` only the user-facing sections that the guide now fully covers (how to use, setup steps for users, settings, reading results).
- Keep developer notes, the method specification, provenance, licence, tests, and any operator/sidecar setup that other docs point to.
- Add a short section near the top, following `modules/temporal_mapper/README.md` ("## Using it"): "The user guide is [`HOW_TO_USE.md`](HOW_TO_USE.md) ... The app shows it behind the **How to use** button on the module page."
- Correct statements that no longer match the code (e.g. "runs on every `log.imported`": module precompute is now lazy and runs when asked; "click Run" when the panel has no such button). Verify each against the code.
- Do not edit `original README.md`, `SIDECAR_SERVICES.md`, or anything under `modules/*/source` or `modules/*/approaches`.

## Report back (this is all the maintainer sees)

For each module: (1) the file path you wrote and its line count; (2) an evidence table, `claim -> file:line`, for every non-obvious claim (defaults, ranges, limits, behaviours, labels you were unsure of); (3) "Unverified/omitted": things you left out because you could not verify them; (4) oddities you noticed (inconsistencies, apparent bugs, stale docs) that you did NOT fix. At most ~80 lines per module. Do not paste the guide into the report.
