# MATE documentation site

The published manual: 29 chapters in two-column pages that follow the landing page's design system, generated from markdown with zero dependencies.

```bash
make docs                                # or: node landing/docs/build.mjs
python3 -m http.server -d landing 8081   # preview → http://localhost:8081/docs/site/introduction.html
```

## The files

| Path | What it is |
| --- | --- |
| `content/NN-*.md` | The source: six numbered files, one per sidebar group, concatenated in filename order. |
| `build.mjs` | The generator: parses the markdown, renders the components below, writes `site/`. |
| `site/` | Generated output — one page per chapter plus `search-index.json`. Never edit by hand. |
| `index.html` | Redirect stub for the bare `/docs/` URL. The entry chapter is `site/introduction.html`. |

## Groups and chapters

| File | Group | Chapters |
| --- | --- | --- |
| `content/00-getting-started.md` | Getting started | Introduction (incl. vocabulary and where-to-start) · Install and start · Your first analysis |
| `content/10-using-mate.md` | Using MATE | Importing event logs · Working with logs · Exploring a process · Modules (incl. the bundled catalogue) · Jobs and the interface · Dashboards · MATE AI · Settings and admin |
| `content/20-platform.md` | Platform | Architecture and data · Jobs, modules and isolation · Security and privacy |
| `content/30-module-development.md` | Module development | Your first module · The manifest · Handlers and the context · Logs, jobs and results · Module communication · Frontend: panels, widgets and datasets · Dependencies, isolation and runtimes · Testing and publishing |
| `content/40-interfaces.md` | Interfaces | REST API · MCP server |
| `content/50-operate.md` | Operate | Deployment · Configuration · Backup, storage and limits · Troubleshooting |
| `content/60-project.md` | Project | Contributing and development |

## Editorial rules

The manual is a working reference, not a tour. Keep it that way:

- One idea per section, one worked example per topic. Delete the second example unless it teaches something the first cannot.
- Prefer a table or a code block over prose. If a paragraph repeats what the code says, cut the paragraph.
- Do not duplicate what another source already owns: the OpenAPI schema (`/openapi.json`), the manifest validator, `manifest.yaml` itself, `.env.example`.
- Every chapter opens with one sentence saying what it is for, and links out instead of re-explaining a neighbouring topic.
- Reference material stays complete but terse; guidance stays short but concrete.

## Writing

One `#` H1 per chapter → one page. `##` and `###` become sections; `####` is available for sub-parts inside a section. `<!-- group: … -->` starts a sidebar group, and a chapter may pin its URL:

```md
# Build a KPI module
<!-- slug: guide-kpi-module -->
```

Pin a slug when other chapters already link to it: without a pin, the slug is derived from the title, so renaming a title changes the URL.

There is no second "on this page" column. The rail carries the whole hierarchy — group, chapter, and the current chapter's sections (`##`, plus `###` nested under them) — and highlights the section you are reading as you scroll. On phones, where the rail is a drawer, the same list appears as a sticky bar under the header.

### Components

````md
> [!NOTE]
> Callouts: NOTE, TIP or WARNING. The first sentence becomes the bold lead.

```bash title="Terminal"
# a fence with a title renders as a code card; without one, the language shows
make up
```

:::steps
1. A numbered rail for procedures. Code fences inside a step stay inside it.
:::

:::cards
- [Label] **Card title** — body text.
- [Label] [Linked card](deployment.html) — links are optional.
:::

:::flow title="Log lifecycle"
`uploading` → `importing` → `processing` → `ready` · modules usable
`importing` → `failed` · the row keeps the reason and a retry
:::

:::stack title="Production topology" caption="One public port."
- Internet · TLS certificate on the edge proxy
- Caddy (container) · TLS termination and path routing
  - `api` · `/api/v1/*`, `/health`
  - `web` · everything else
:::

| Tables | Work |
| --- | --- |
| `GET /event-logs` | A code span starting with an HTTP method renders as a method chip. |
| [[foundation]] | Double brackets render a small metadata chip. |
| <kbd>cmd</kbd> + <kbd>K</kbd> | Keycaps work inline. |
````

Code cards key their header and comment styling off the fence tag: `bash` reads *Terminal*, `tree` renders a file tree with dimmed connectors and trailing notes, and whole-line `#` or `//` comments dim out. Anything else falls back to the raw tag.

### Diagrams

Two directives render diagrams as themed DOM instead of ASCII art — no runtime JavaScript, no external library, dark-mode aware, printable, and readable by a screen reader in source order. Both take `title="…"` and `caption="…"`.

| Directive | Shape | Author it as |
| --- | --- | --- |
| `:::flow` | One chain per line, arrows between nodes | `` `a` → `b` → `c` · note on the last node `` |
| `:::stack` | Vertical layers, each branching into a fan-out | A nested list: `- Layer · note` with indented `- Child · note` |

A node is `Label`, `` `code` `` or `Label · muted note`, so units and paths belong in the note rather than the label. File trees stay ```` ```tree ```` fences — indentation is the point there, and the builder already dims connectors and trailing comments.

### Links

- Repository files: absolute GitHub URLs (`https://github.com/Process-Science-Community/MATE/blob/main/…`).
- Other chapters: `chapter-slug.html`, optionally with `#section-id`.
- `documentation-map.html` is generated and lists every chapter as a card grid per group.

## Conventions

- Chapter slugs are public URLs. Pin them before renaming a chapter that is linked elsewhere.
- Cross-chapter references use the chapter title as the link text, so the rail, the pager and the prose agree.
- One idea per section. Code before prose when a task has steps. End a chapter that opens a new area with a `:::cards` "where to go next" block.
- After a content change, scan `site/` for links whose target page or `#anchor` does not exist — the site is static, so a typo is a dead link rather than an error.

## Related

- [`docs/README.md`](../../docs/README.md) — the index of every document in the repository.
- [`docs/DEPLOY.md`](../../docs/DEPLOY.md), [`modules/README.md`](../../modules/README.md) — the two documents this manual links to most often.
