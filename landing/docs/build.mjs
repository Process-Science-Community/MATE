#!/usr/bin/env node
/**
 * MATE documentation builder.
 *
 * Reads docs/content/*.md in filename order — one H1 per chapter, sidebar group
 * set via `<!-- group: … -->`, URL pinned via `<!-- slug: … -->` — and writes
 * docs/site/: one page per chapter, in the landing page's design system and the
 * docs layout of Starlight/Fumadocs.
 *
 *   - <slug>.html         three-column docs page: group rail (search trigger,
 *                         nested sections of the current chapter) · article ·
 *                         "On this page", plus breadcrumb and prev/next pager
 *
 *   - search-index.json   fetched on first Ctrl+K, so no index in the pages
 *
 * Content components (see README.md for the authoring syntax): code cards with
 * a title strip and comment dimming, callouts with icon badges, :::steps rails,
 * :::cards grids, method chips for HTTP routes, [[chips]] for metadata, tables,
 * file trees, and key caps.
 *
 * There is no index.html: /docs/ enters at the first chapter. See
 * ../docs/index.html, the redirect stub for the bare /docs/ URL.
 *
 * Anatomy and motion are lifted from Starlight and Fumadocs:
 *   - grid + fixed side rails, narrow measure (Starlight's 3-column formulas,
 *     fumadocs' 268px rails), sized here to the landing page's 1180px container
 *     so the top bar, rail and TOC share one column
 *   - sidebar: <details> groups (folds without JS) animated 150ms
 *     cubic-bezier(.45,0,.55,1) like fumadocs; `aria-current` accent pill with a
 *     1px accent bar; scroll position kept in sessionStorage like starlight
 *   - on-this-page: fumadocs' guide spine + a dot that travels to the active
 *     heading, 150ms; headings activate on a scroll listener whose threshold is
 *     the live header height + 24px (starlight derives an equivalent rootMargin
 *     for its IntersectionObserver)
 *   - drawer 250ms ease over a 300ms overlay fade; search dialog 300ms
 *     cubic-bezier(.16,1,.3,1); view transitions for the theme switch
 *
 * Deliberate divergences from both references: `prefers-reduced-motion` guards
 * every animation (starlight guards one of four, fumadocs none), and the
 * sidebar/drawer manage focus and body scroll explicitly.
 *
 * The shell — tokens, background, sticky top bar, buttons, cards, hero, footer —
 * is still a verbatim copy of ../index.html. Change one file, change the other.
 *
 * Zero dependencies: `node landing/docs/build.mjs` (or `make docs`).
 */
import { mkdirSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const SRC = join(HERE, "content");
const OUT = join(HERE, "site");

/* ---------- helpers ---------- */

const esc = (s) =>
  String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");

const slug = (s) =>
  s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "") || "chapter";

/** Links are absolute (GitHub or external) or in-site chapter slugs; anything
 *  else is resolved relative to the published docs folder as a fallback. */
function linkHref(url) {
  if (/^(https?:|mailto:|#)/.test(url)) return url;
  if (url.endsWith(".html")) return url; // intra-site
  return "../" + url.replace(/^\.\//, ""); // repo/docs-relative
}

const plainText = (s) => s.replace(/\*\*|`/g, "").replace(/\[([^\]]+)\]\([^)]*\)/g, "$1");

/* ---------- inline markdown ---------- */

function inline(src) {
  let s = esc(src);
  const codes = [];
  s = s.replace(/`([^`]+)`/g, (_, c) => {
    codes.push(c);
    return `\u0000${codes.length - 1}\u0000`;
  });
  // whitelisted raw HTML: <kbd>
  s = s.replace(/&lt;kbd&gt;([\s\S]*?)&lt;\/kbd&gt;/g, "<kbd>$1</kbd>");
  // [[text]] renders a small chip — used for categories, runtimes, statuses
  s = s.replace(/\[\[([^\]]+)\]\]/g, '<span class="chip">$1</span>');
  s = s.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (_, t, u) => `<a href="${linkHref(u)}">${t}</a>`);
  s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  s = s.replace(/(^|[^*\w])\*([^*\n]+)\*(?!\w)/g, "$1<em>$2</em>");
  // A code span that opens with an HTTP method becomes a chip plus the path
  // ("`GET /event-logs`") — the REST chapter is a long table of them.
  s = s.replace(/\u0000(\d+)\u0000/g, (_, i) => {
    const c = codes[+i];
    const m = c.match(/^(GET|POST|PUT|PATCH|DELETE) (\S.*)$/);
    return m
      ? `<span class="mchip m-${m[1].toLowerCase()}">${m[1]}</span><code>${m[2]}</code>`
      : `<code>${c}</code>`;
  });
  return s;
}

/* ---------- parsing ---------- */

function parseChapters(md) {
  const chapters = [];
  const lines = md.split(/\r?\n/);
  let group = "Documentation";
  let cur = null;
  let inFence = false;
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (/^```/.test(line)) {
      inFence = !inFence;
      if (cur) cur.blocks.push(line);
      continue;
    }
    if (inFence) {
      if (cur) cur.blocks.push(line);
      continue;
    }
    const g = line.match(/^<!--\s*group:\s*(.+?)\s*-->/);
    if (g) {
      group = g[1];
      continue;
    }
    if (/^#\s+/.test(line)) {
      /* A chapter may pin its URL with `<!-- slug: my-slug -->` on the line
         right after the title. Titles stay free to change; the slug — which is
         the public URL and every cross-link target — does not. */
      const pinned = (lines[i + 1] || "").match(/^<!--\s*slug:\s*([a-z0-9-]+)\s*-->/);
      cur = {
        title: line.replace(/^#\s+/, "").trim(),
        group,
        blocks: [],
        slug: pinned ? pinned[1] : null,
      };
      chapters.push(cur);
      if (pinned) i++; // consume the slug comment
      continue;
    }
    if (cur) cur.blocks.push(line);
  }
  return chapters;
}

const isItem = (l) => /^\s*(?:[-*]|\d+\.)\s+/.test(l);

function parseBlocks(lines) {
  const blocks = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) {
      i++;
      continue;
    }
    // fenced code — ```` ```lang title="path" ```` becomes the card's header,
    // and a directive fence (:::steps / :::cards) parses its body recursively
    if (line.startsWith("```")) {
      const m = line.match(/^```(\S*)\s*(.*)$/);
      const lang = (m ? m[1] : "").toLowerCase();
      const meta = m ? m[2].trim() : "";
      const tm = meta.match(/title\s*=\s*"([^"]*)"/) || meta.match(/^\[(.*)\]$/);
      const buf = [];
      i++;
      while (i < lines.length && !lines[i].startsWith("```")) {
        buf.push(lines[i]);
        i++;
      }
      i++;
      blocks.push({ t: "code", lang, title: tm ? tm[1] : null, code: buf.join("\n") });
      continue;
    }
    if (line.startsWith(":::")) {
      const dm = line.match(/^:::\s*([a-z-]+)\s*(.*)$/);
      const buf = [];
      i++;
      while (i < lines.length && lines[i].trim() !== ":::") {
        buf.push(lines[i]);
        i++;
      }
      i++;
      blocks.push({
        t: "directive",
        name: dm ? dm[1] : "",
        arg: dm ? dm[2].trim() : "",
        raw: buf,
        blocks: parseBlocks(buf),
      });
      continue;
    }
    // headings — ## … #### (##### and deeper stay literal text)
    const h = line.match(/^(#{2,4})\s+(.*)$/);
    if (h) {
      blocks.push({ t: "h" + h[1].length, text: h[2].trim() });
      i++;
      continue;
    }
    // callouts / quotes
    if (line.startsWith(">")) {
      const buf = [];
      while (i < lines.length && lines[i].startsWith(">")) {
        buf.push(lines[i].replace(/^>\s?/, ""));
        i++;
      }
      const first = (buf[0] || "").trim();
      const m = first.match(/^\[!(NOTE|TIP|WARNING)\]\s*(.*)$/i);
      const text = [m ? m[2] : first, ...buf.slice(1)].filter((x) => x.trim()).join(" ");
      blocks.push({ t: "callout", kind: m ? m[1].toUpperCase() : "NOTE", text });
      continue;
    }
    // tables
    if (line.trim().startsWith("|")) {
      const rows = [];
      while (i < lines.length && lines[i].trim().startsWith("|")) {
        rows.push(lines[i].trim());
        i++;
      }
      blocks.push({ t: "table", rows });
      continue;
    }
    // lists (with indented code + continuations inside items)
    if (isItem(line)) {
      const ordered = /^\s*\d+\.\s/.test(line);
      const items = [];
      while (i < lines.length) {
        const l = lines[i];
        if (isItem(l)) {
          items.push({ text: l.replace(/^\s*(?:[-*]|\d+\.)\s+/, ""), code: null });
          i++;
          continue;
        }
        if (!l.trim()) {
          let j = i + 1;
          while (j < lines.length && !lines[j].trim()) j++;
          const next = lines[j] || "";
          if (isItem(next) || /^\s{2,}\S/.test(next)) {
            i = j;
            continue;
          }
          break;
        }
        if (items.length && /^\s{2,}```/.test(l)) {
          const lm = l.trim().match(/^```(\S*)\s*(.*)$/);
          const lmeta = lm ? lm[2].trim() : "";
          const ltm = lmeta.match(/title\s*=\s*"([^"]*)"/) || lmeta.match(/^\[(.*)\]$/);
          i++;
          const buf = [];
          while (i < lines.length && !lines[i].trim().startsWith("```")) {
            buf.push(lines[i].replace(/^ {3}/, ""));
            i++;
          }
          i++;
          items[items.length - 1].code = {
            lang: lm ? lm[1].toLowerCase() : "",
            title: ltm ? ltm[1] : null,
            code: buf.join("\n"),
          };
          continue;
        }
        if (items.length && /^\s{2,}\S/.test(l)) {
          items[items.length - 1].text += " " + l.trim();
          i++;
          continue;
        }
        break;
      }
      blocks.push({ t: ordered ? "ol" : "ul", items });
      continue;
    }
    // paragraph
    const buf = [];
    while (
      i < lines.length &&
      lines[i].trim() &&
      !/^(#{1,4}\s|```|>|:::)/.test(lines[i]) &&
      !lines[i].trim().startsWith("|") &&
      !isItem(lines[i])
    ) {
      buf.push(lines[i].trim());
      i++;
    }
    blocks.push({ t: "p", text: buf.join(" ") });
  }
  return blocks;
}

/* ---------- block rendering ---------- */

/* Language labels for the code card's header; anything unknown falls back to
   the raw fence tag. Bash-family fences read as "Terminal". */
const LANG_LABEL = {
  bash: "Terminal", sh: "Terminal", shell: "Terminal", console: "Terminal", zsh: "Terminal",
  text: "Text", tree: "File tree", md: "Markdown", markdown: "Markdown",
  py: "Python", python: "Python", yaml: "YAML", yml: "YAML", json: "JSON",
  ts: "TypeScript", tsx: "TSX", js: "JavaScript", jsx: "JSX", java: "Java",
  sql: "SQL", html: "HTML", css: "CSS",
};

/**
 * Cheap, safe emphasis inside a code block: whole-line comments dim out, and a
 * file tree gets its connectors and trailing notes dimmed. Nothing else is
 * touched — no syntax highlighter, so nothing can be mis-coloured.
 */
function codeBody(b) {
  const esc2 = (s) => esc(s).replace(/&#39;|&quot;/g, (m) => m);
  return b.code
    .split("\n")
    .map((line) => {
      let html = esc(line);
      if (b.lang === "tree") {
        html = html.replace(/^([\s\u00a0]*)([├└│─]+)/, (_, pad, glyph) => `${pad}<span class="t-glyph">${glyph}</span>`);
        html = html.replace(/(\s{2,})(#\s.*)$/, (_, pad, note) => `${pad}<span class="t-note">${note}</span>`);
      }
      if (/^\s*(#|\/\/)\s?/.test(line)) {
        html = `<span class="t-note">${html}</span>`;
      }
      return html;
    })
    .join("\n");
}

function renderCode(b) {
  const label = b.title || LANG_LABEL[b.lang] || (b.lang ? b.lang.toUpperCase() : "Code");
  return `<figure class="codeblock"${b.lang ? ` data-lang="${esc(b.lang)}"` : ""}>
  <figcaption class="cb-head"><span class="cb-title">${esc(label)}</span></figcaption>
  <pre><code>${codeBody(b)}</code></pre>
</figure>`;
}

function renderListItems(items) {
  return items
    .map((it) => `<li>${inline(it.text)}${it.code ? renderCode(it.code) : ""}</li>`)
    .join("");
}

function renderTable(rows) {
  const cells = (r) =>
    r
      .replace(/^\|/, "")
      .replace(/\|$/, "")
      .split("|")
      .map((c) => c.trim());
  const head = cells(rows[0]);
  const body = rows.slice(1).filter((r) => !/^[\s|:-]+$/.test(r)).map(cells);
  let html = `<div class="tbl"><div class="tbl-scroll"><table><thead><tr>`;
  html += head.map((c) => `<th>${inline(c)}</th>`).join("");
  html += `</tr></thead><tbody>`;
  for (const r of body) html += `<tr>${r.map((c) => `<td>${inline(c)}</td>`).join("")}</tr>`;
  html += `</tbody></table></div></div>`;
  return html;
}

const ICON_INFO = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 11v5"/><path d="M12 8h.01"/></svg>`;
const ICON_TIP = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 18h6"/><path d="M10 22h4"/><path d="M12 2a7 7 0 0 0-4 12.7V15h8v-.3A7 7 0 0 0 12 2Z"/></svg>`;
const ICON_WARN = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M10.3 3.9 2 18a2 2 0 0 0 1.7 3h16.6a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z"/><path d="M12 9v4"/><path d="M12 17h.01"/></svg>`;

const CALLOUT_LABEL = { NOTE: "Note", TIP: "Tip", WARNING: "Warning" };
const CALLOUT_ICON = { NOTE: ICON_INFO, TIP: ICON_TIP, WARNING: ICON_WARN };

function renderCallout(kind, text) {
  let body = inline(text);
  const m = text.match(/^([^.]{1,40})\.\s+(.*)$/s);
  if (m) body = `<strong>${inline(m[1])}.</strong> ${inline(m[2])}`;
  return `<aside class="callout callout-${kind.toLowerCase()}"><span class="callout-icon" aria-hidden="true">${
    CALLOUT_ICON[kind] || ICON_INFO
  }</span><div class="callout-body"><span class="label">${CALLOUT_LABEL[kind]}</span><p>${body}</p></div></aside>`;
}

/**
 * Card grid — the landing page's `.prop` card, in two shapes:
 *   `[Label] **Title** — one line of text`   → an info card
 *   `[Label] [Title](target.html) — text`    → a linked card
 * Used for "where to go next" blocks and for the platform properties.
 */
function renderCardGrid(items) {
  const cards = items
    .map((it) => {
      const m = it.text.match(
        /^\[([^\]]+)\]\s*(?:\*\*([^*]+)\*\*|\[([^\]]+)\]\(([^)\s]+)\))\s*([\s\S]*)$/
      );
      if (!m) return "";
      const [, label, strong, linkText, href] = m;
      const rest = (m[5] || "").replace(/^\s*[—–-]\s*/, "");
      const title = strong || linkText;
      const inner = `<span class="label">${esc(label)}</span><h3>${inline(title)}</h3>${
        rest ? `<p>${inline(rest)}</p>` : ""
      }`;
      return href
        ? `<a class="card" href="${linkHref(href)}">${inner}</a>`
        : `<div class="card">${inner}</div>`;
    })
    .join("");
  return `<div class="cards">${cards}</div>`;
}

const isProps = (items) =>
  items.length >= 2 && items.every((it) => /^\[[^\]]+\]\s*(\*\*|\[)/.test(it.text));

/** `:::cards` — the same grid, written as a directive over a markdown list. */
function renderCardGridFromBlocks(blocks) {
  const list = blocks.find((b) => b.t === "ul" || b.t === "ol");
  return list ? renderCardGrid(list.items) : "";
}

/** `:::steps` — a numbered rail for procedures, with code kept inside the step. */
function renderSteps(blocks) {
  const list = blocks.find((b) => b.t === "ol" || b.t === "ul");
  if (!list) return blocks.map((b) => renderBlock(b, new Set())).join("\n");
  const steps = list.items
    .map(
      (it, n) =>
        `    <li class="step"><span class="step-num" aria-hidden="true">${n + 1}</span><div class="step-body"><p>${inline(
          it.text
        )}</p>${it.code ? renderCode(it.code) : ""}</div></li>`
    )
    .join("\n");
  return `<ol class="steps">\n${steps}\n  </ol>`;
}

function renderHeading(b, ids) {
  const base = slug(plainText(b.text));
  let id = base;
  let n = 2;
  while (ids.has(id)) id = `${base}-${n++}`;
  ids.add(id);
  return `<${b.t} id="${id}">${inline(b.text)}<a class="h-anchor" href="#${id}" aria-label="Link to this section">#</a></${b.t}>`;
}

/* ---------- diagrams ----------
   ASCII art in a code card reads as code and breaks on a phone. Diagrams are
   authored declaratively instead (`:::flow` for chains, `:::stack` for layered
   topologies) and rendered as real DOM: themed by the same CSS variables as the
   rest of the site, responsive without horizontal scrolling, readable by a
   screen reader in source order, and printable. */

const ARROW = `<svg class="dg-arrow" viewBox="0 0 26 10" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M0 5h22"/><path d="m18 1.5 4 3.5-4 3.5"/></svg>`;

/** Directive args: `title="…" caption="…"`. */
function directiveMeta(arg) {
  const grab = (key) => {
    const m = (arg || "").match(new RegExp(`${key}\\s*=\\s*"([^"]*)"`));
    return m ? m[1] : "";
  };
  return { title: grab("title"), caption: grab("caption") };
}

/** `Label`, `Label · note`, or `\`code\` · note` — split on the first ·. */
function diagramNode(text) {
  const [label, ...rest] = text.split(/\s+·\s+/);
  const note = rest.join(" · ").trim();
  return `<span class="dg-node"><span class="dg-label">${inline(label.trim())}</span>${
    note ? `<span class="dg-note">${inline(note)}</span>` : ""
  }</span>`;
}

function diagramFigure(kind, raw, arg) {
  const { title, caption } = directiveMeta(arg);
  const body = kind === "flow" ? renderFlow(raw) : renderStack(raw);
  return `<figure class="diagram diagram-${kind}">
  <div class="dg-canvas">
${body}
  </div>${
    caption
      ? `\n  <figcaption class="dg-cap">${title ? `<b>${esc(title)}.</b> ` : ""}${inline(
          caption
        )}</figcaption>`
      : ""
  }
</figure>`;
}

/** `:::flow` — one chain per line: `a → b → c`, each node optionally annotated. */
function renderFlow(raw) {
  const rows = raw
    .filter((line) => line.trim())
    .map((line) =>
      line
        .split(/\s*(?:→|->|-->)\s*/)
        .map((part) => part.trim())
        .filter(Boolean)
    )
    .filter((parts) => parts.length);
  return rows
    .map(
      (parts) =>
        `    <div class="dg-row">${parts
          .map((p) => diagramNode(p))
          .join(ARROW)}</div>`
    )
    .join("\n");
}

/** `:::stack` — a nested list: each top-level item is a layer, its children a
 *  fan-out beneath it. Chains of responsibility, top to bottom. */
function renderStack(raw) {
  const nodes = [];
  for (const line of raw) {
    if (!line.trim()) continue;
    const indent = line.match(/^\s*/)[0].length;
    const text = line.trim().replace(/^[-*]\s+/, "");
    const node = { text, indent, children: [] };
    const parent = [...nodes].reverse().find((n) => n.indent < indent);
    if (parent) parent.children.push(node);
    else nodes.push(node);
  }
  return nodes
    .map((node) => {
      const kids = node.children.length
        ? `\n      <div class="dg-children">${node.children
            .map((c) => `<span class="dg-child">${diagramNode(c.text)}</span>`)
            .join("")}</div>`
        : "";
      return `    <div class="dg-layer">${diagramNode(node.text)}${kids}</div>`;
    })
    .join('\n    <div class="dg-connector" aria-hidden="true"></div>\n');
}

function renderDirective(b, ids) {
  if (b.name === "steps") return renderSteps(b.blocks);
  if (b.name === "cards") return renderCardGridFromBlocks(b.blocks);
  if (b.name === "flow" || b.name === "stack")
    return diagramFigure(b.name, b.raw || [], b.arg);
  /* an unknown directive renders its contents rather than swallowing them */
  return b.blocks.map((x) => renderBlock(x, ids)).join("\n");
}

function renderBlock(b, ids) {
  switch (b.t) {
    case "h2":
    case "h3":
    case "h4":
      return renderHeading(b, ids);
    case "p":
      return `<p>${inline(b.text)}</p>`;
    case "code":
      return renderCode(b);
    case "table":
      return renderTable(b.rows);
    case "callout":
      return renderCallout(b.kind, b.text);
    case "ul":
      return isProps(b.items)
        ? renderCardGrid(b.items)
        : `<ul>${renderListItems(b.items)}</ul>`;
    case "ol":
      return `<ol>${renderListItems(b.items)}</ol>`;
    case "directive":
      return renderDirective(b, ids);
    default:
      return "";
  }
}

/** hero = lead before the first non-prose block; body = the rest, flat. */
function splitChapter(blocks) {
  const hero = [];
  const body = [];
  let inBody = false;
  for (const b of blocks) {
    if (!inBody && b.t !== "p" && b.t !== "callout") inBody = true;
    (inBody ? body : hero).push(b);
  }
  return { hero, body };
}

function snippet(ch) {
  const firstP = ch.blocks.find((b) => b.t === "p" && b.text.trim());
  const t = plainText(firstP ? firstP.text : "");
  return t.length > 150 ? t.slice(0, 147).replace(/\s+\S*$/, "") + "…" : t;
}

/* ---------- page shell — the landing page's chrome, verbatim ---------- */

/* the landing page's mark, two levels up from site/ — same asset, same rules */
const ICON = "../../assets/mate-icon-black.svg";

const LOGO = `<img class="logo-dark-invert" src="${ICON}" alt="">`;

const MENU_ICON = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M4 6h16M4 12h16M4 18h16"/></svg>`;
const SEARCH_ICON = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/></svg>`;
const CARET_ICON = `<svg class="caret" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m9 18 6-6-6-6"/></svg>`;
const CHEV_ICON = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m9 18 6-6-6-6"/></svg>`;
const PREV_ICON = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m15 18-6-6 6-6"/></svg>`;
const NEXT_ICON = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m9 18 6-6-6-6"/></svg>`;

const CSS = `
/* NOTE: this is a JS template literal. A backtick or \${ anywhere below ends it
   and produces a confusing syntax error — write comments without them. */
:root {
  --bg: #fdfdfd; --surface: #ffffff; --panel: #f7f7f9; --card: #ffffff;
  --ink: #17181c; --ink-soft: #494c57; --muted: #6b6f7d;
  --line: #e7e8ee; --line-soft: #f0f1f5;
  --btn-bg: #141414; --btn-ink: #ffffff; --chip-bg: rgba(255, 255, 255, 0.75);
  --header-bg: rgba(253, 253, 253, 0.82);
  --lav: rgba(196, 181, 253, 0.55); --rose: rgba(247, 200, 221, 0.6); --sky: rgba(186, 210, 253, 0.45);
  --av-lav: rgba(196, 181, 253, 0.35); --av-rose: rgba(247, 200, 221, 0.4); --av-sky: rgba(186, 210, 253, 0.35);
  --sans: ui-sans-serif, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", sans-serif;
  --mono: ui-monospace, "SF Mono", SFMono-Regular, Menlo, Consolas, monospace;
  --maxw: 1180px; --radius: 14px;
  --card-shadow: 0 1px 2px rgba(23, 24, 28, 0.05), 0 8px 28px rgba(23, 24, 28, 0.06);
  --frame-shadow: 0 2px 6px rgba(23, 24, 28, 0.06), 0 16px 48px rgba(23, 24, 28, 0.10);
  --hero-shadow: 0 2px 6px rgba(23, 24, 28, 0.06), 0 24px 70px rgba(23, 24, 28, 0.13);

  /* --- docs shell ---------------------------------------------------------
     One rail, one article. The rail carries the whole hierarchy — group →
     chapter → section — so there is no second "on this page" column; the
     article is centred in what remains of the landing page's 1180px container. */
  --nav-h: 60px;
  --docs-side: 236px; --docs-gap: 24px;
  /* accent: the landing page's lavender, dialled down to a working UI colour */
  --accent: #6d4de0; --accent-soft: rgba(109, 77, 224, 0.09);
  --c-note: #3f6fd8; --c-note-soft: rgba(63, 111, 216, 0.09);
  --c-tip: #2b8f63; --c-tip-soft: rgba(43, 143, 99, 0.09);
  --c-warn: #b45309; --c-warn-soft: rgba(180, 83, 9, 0.10);
  --c-danger: #ad3a5e; --c-danger-soft: rgba(173, 58, 94, 0.09);
  /* motion tokens, named after the ones in fumadocs' base.css */
  --ease-collapse: cubic-bezier(0.45, 0, 0.55, 1);
  --ease-dialog: cubic-bezier(0.16, 1, 0.3, 1);
  --ease-base: cubic-bezier(0.4, 0, 0.2, 1);
}
[data-theme="dark"] {
  --bg: #0e0e12; --surface: #121217; --panel: #14141a; --card: #17171d;
  --ink: #f0f0f4; --ink-soft: #b4b6c1; --muted: #8b8e9b;
  --line: #26262e; --line-soft: #1d1d24;
  --btn-bg: #f0f0f4; --btn-ink: #141414; --chip-bg: rgba(23, 23, 29, 0.75);
  --header-bg: rgba(14, 14, 18, 0.82);
  --lav: rgba(110, 84, 210, 0.28); --rose: rgba(170, 50, 110, 0.2); --sky: rgba(60, 90, 190, 0.2);
  --av-lav: rgba(110, 84, 210, 0.32); --av-rose: rgba(170, 50, 110, 0.28); --av-sky: rgba(60, 90, 190, 0.3);
  --c-warn: #fbbf24; --c-warn-soft: rgba(251, 191, 36, 0.14);
  --c-danger: #f2a7c0; --c-danger-soft: rgba(242, 167, 192, 0.13);
  --accent: #a78bfa; --accent-soft: rgba(167, 139, 250, 0.13);
  --c-note: #7fa4ff; --c-note-soft: rgba(127, 164, 255, 0.13);
  --c-tip: #63d3a2; --c-tip-soft: rgba(99, 211, 162, 0.13);
  --card-shadow: 0 1px 2px rgba(0, 0, 0, 0.3), 0 8px 28px rgba(0, 0, 0, 0.35);
  --frame-shadow: 0 2px 6px rgba(0, 0, 0, 0.4), 0 16px 48px rgba(0, 0, 0, 0.45);
  --hero-shadow: 0 2px 6px rgba(0, 0, 0, 0.4), 0 24px 70px rgba(0, 0, 0, 0.5);
}
* { margin: 0; padding: 0; box-sizing: border-box; }
html { scroll-behavior: smooth; }
body {
  font-family: var(--sans); background: var(--bg); color: var(--ink);
  line-height: 1.65; font-size: 16.5px;
  -webkit-font-smoothing: antialiased;
}
html.theme-snap *, html.theme-snap *::before, html.theme-snap *::after { transition: none !important; }
a { color: var(--ink); text-decoration: none; }
a:hover { text-decoration: underline; text-underline-offset: 3px; }
code, pre { font-family: var(--mono); }
.wrap { max-width: var(--maxw); margin: 0 auto; padding: 0 28px; }
::selection { background: rgba(196, 181, 253, 0.45); }

/* reveal-on-scroll was only used by the splash index; the docs pages have no
   reveal targets, so the rule and its observer are gone. */

/* keyboard focus: visible ring on every interactive element */
:focus-visible { outline: 2px solid var(--ink-soft); outline-offset: 2px; }

/* theme change: crossfade the whole page instead of an abrupt brightness jump */
::view-transition-old(root) { animation: vt-fade-out 220ms ease; mix-blend-mode: normal; }
::view-transition-new(root) { animation: vt-fade-in 220ms ease; mix-blend-mode: normal; }
@keyframes vt-fade-out { to { opacity: 0; } }
@keyframes vt-fade-in { from { opacity: 0; } }
.label {
  font-family: var(--mono); font-size: 11px; font-weight: 600;
  letter-spacing: 0.14em; text-transform: uppercase; color: var(--muted);
}
.label-warn { color: var(--c-warn); }

/* ---------- marketing blocks — verbatim from ../index.html ---------- */
.hero { position: relative; padding: 88px 0 72px; text-align: center; overflow: hidden; }
.hero::before {
  content: ""; position: absolute; inset: -20% -10%; pointer-events: none; z-index: 0;
  background:
    radial-gradient(ellipse 560px 420px at 24% 30%, var(--lav), transparent 68%),
    radial-gradient(ellipse 620px 460px at 76% 26%, var(--rose), transparent 70%),
    radial-gradient(ellipse 520px 420px at 55% 72%, var(--sky), transparent 70%);
  filter: blur(46px);
}
.hero > .wrap { position: relative; z-index: 1; }
.hero-chip {
  display: inline-flex; align-items: center; gap: 8px;
  font-size: 13px; font-weight: 550; color: var(--ink-soft);
  background: var(--chip-bg); border: 1px solid var(--line);
  border-radius: 999px; padding: 6px 16px; margin-bottom: 26px;
  box-shadow: 0 1px 4px rgba(23, 24, 28, 0.05);
}
.hero-chip .dot { width: 7px; height: 7px; border-radius: 50%; background: #a78bfa; }
h1 {
  font-size: clamp(36px, 5vw, 58px);
  line-height: 1.06; letter-spacing: -0.028em; font-weight: 700;
  max-width: 17em; margin: 0 auto;
}
.hero p.sub { margin: 22px auto 0; font-size: 18px; color: var(--ink-soft); max-width: 38em; }
.cta-row { display: flex; gap: 12px; margin-top: 32px; flex-wrap: wrap; align-items: center; justify-content: center; }
.home section { padding: 92px 0; }
.sec-head { max-width: 44em; margin: 0 auto 48px; text-align: center; }
.sec-head .label { display: inline-block; margin-bottom: 14px; }
h2 { font-size: clamp(28px, 3.4vw, 40px); letter-spacing: -0.025em; line-height: 1.12; font-weight: 700; }
.sec-head p { margin-top: 14px; color: var(--ink-soft); font-size: 17px; }
.closing { position: relative; overflow: hidden; text-align: center; }
.closing::before {
  content: ""; position: absolute; inset: -30% -10%; pointer-events: none; z-index: 0;
  background:
    radial-gradient(ellipse 620px 420px at 30% 60%, var(--lav), transparent 68%),
    radial-gradient(ellipse 620px 440px at 72% 45%, var(--rose), transparent 70%);
  filter: blur(48px);
}
.closing > .wrap { position: relative; z-index: 1; }
.closing h2 { max-width: 16em; margin: 0 auto; }
.closing p { margin: 16px auto 0; color: var(--ink-soft); font-size: 17px; max-width: 34em; }

/* header — the site chrome, Docs tab active */
header {
  position: sticky; top: 0; z-index: 50;
  background: var(--header-bg); backdrop-filter: blur(12px);
  border-bottom: 1px solid transparent;
  transition: border-color 200ms ease;
}
header.scrolled { border-bottom-color: var(--line-soft); }
.nav { display: flex; align-items: center; height: 60px; gap: 26px; }
.brand { display: flex; align-items: center; gap: 11px; font-weight: 650; font-size: 17px; letter-spacing: -0.015em; }
.brand:hover { text-decoration: none; }
.brand img { width: 22px; height: 22px; }
[data-theme="dark"] .logo-dark-invert { filter: invert(1); }
.nav-links { display: flex; gap: 24px; margin-left: auto; font-size: 14.5px; font-weight: 500; align-items: center; }
.nav-links a { color: var(--ink-soft); }
.nav-links a:hover { color: var(--ink); text-decoration: none; }
.nav-links a.active { color: var(--ink); font-weight: 650; }
.btn {
  display: inline-flex; align-items: center; justify-content: center; gap: 9px;
  padding: 11px 22px; font-size: 15px; font-weight: 600; border-radius: 10px;
  border: 1px solid transparent; cursor: pointer; font-family: inherit;
  transition: transform 140ms ease-out, box-shadow 140ms ease-out, background 140ms ease-out;
}
.btn:active { transform: scale(0.97); transition-duration: 60ms; }
.btn-black { background: var(--btn-bg); color: var(--btn-ink); }
.btn-ghost { border-color: var(--line); color: var(--ink); background: var(--card); }
.nav .btn { padding: 8px 16px; font-size: 14px; min-height: 44px; }
.nav-links a.btn-black, .nav-links a.btn-black:hover { color: var(--btn-ink); }
.theme-toggle, .menu-btn {
  background: transparent; border: 1px solid var(--line); border-radius: 8px;
  height: 40px; cursor: pointer; color: var(--ink-soft);
  display: inline-flex; align-items: center; justify-content: center; gap: 8px;
  font-family: inherit; transition: background 140ms ease-out, color 140ms ease-out;
}
.theme-toggle { width: 40px; font-size: 15px; line-height: 1; }
.menu-btn { width: 40px; display: none; }
.menu-btn svg { width: 17px; height: 17px; }
.theme-toggle:active, .menu-btn:active { background: var(--panel); }
@media (hover: hover) {
  .btn:hover { text-decoration: none; transform: translateY(-1px); }
  .btn-black:hover { box-shadow: 0 6px 20px rgba(20, 20, 20, 0.25); }
  .btn-ghost:hover { box-shadow: 0 4px 14px rgba(23, 24, 28, 0.12); }
  .theme-toggle:hover, .menu-btn:hover { background: var(--panel); color: var(--ink); }
}
@media (max-width: 820px) { .nav-links a:not(.btn-black) { display: none; } }

/* ==========================================================================
   Docs shell — Starlight/Fumadocs anatomy inside the landing page's design
   system: one 1180px container, a sticky rail, and the article.
   ========================================================================== */
.docs {
  max-width: var(--maxw); margin: 0 auto; padding: 0 28px;
  display: grid; grid-template-columns: var(--docs-side) minmax(0, 1fr);
  gap: 0 var(--docs-gap); align-items: start;
}

/* --- left rail ---------------------------------------------------------- */
.side {
  position: sticky; top: var(--nav-h); align-self: start;
  max-height: calc(100dvh - var(--nav-h));
  overflow-y: auto; overscroll-behavior: contain;
  padding: 32px 0 48px; scrollbar-width: thin;
  /* fumadocs fades scroll containers at the edges instead of drawing a border */
  mask-image: linear-gradient(to bottom, transparent, #000 16px, #000 calc(100% - 16px), transparent);
}
.side::-webkit-scrollbar { width: 6px; }
.side::-webkit-scrollbar-thumb { background: var(--line); border-radius: 999px; }
.side-group { border: 0; }
.side-group + .side-group { margin-top: 22px; }
.side-group > summary {
  display: flex; align-items: center; justify-content: space-between; gap: 8px;
  padding: 5px 10px; border-radius: 8px; cursor: pointer; user-select: none;
  list-style: none;
  font-family: var(--mono); font-size: 11px; font-weight: 600;
  letter-spacing: 0.14em; text-transform: uppercase; color: var(--muted);
}
.side-group > summary::-webkit-details-marker { display: none; }
.side-group > summary:hover { color: var(--ink-soft); }
.side-group .caret { flex-shrink: 0; transition: transform 200ms ease-in-out; }
.side-group[open] .caret { transform: rotate(90deg); }
.side-group > ul { list-style: none; padding: 2px 0 0; }
.side-link {
  position: relative; display: block; padding: 6px 10px; border-radius: 8px;
  font-size: 14px; line-height: 1.45; color: var(--ink-soft);
  transition: background-color 150ms var(--ease-base), color 150ms var(--ease-base);
}
/* fumadocs' trick: hover-in is instant, hover-out animates */
.side-link:hover { background: var(--panel); color: var(--ink); text-decoration: none; transition: none; }
.side-link[aria-current="page"] { background: var(--accent-soft); color: var(--ink); font-weight: 600; }
.side-link[aria-current="page"]::before {
  content: ""; position: absolute; inset-block: 7px; inset-inline-start: 0;
  width: 1px; border-radius: 1px; background: var(--accent);
}
/* the current chapter's own sections — this rail is the page's only table of
   contents, so it carries both heading levels and marks the active one */
.side-sub {
  list-style: none; margin: 2px 0 8px; padding: 0 0 0 11px; border: 0;
  border-left: 1px solid var(--line);
}
.side-sub a {
  display: block; padding: 4px 8px; border-radius: 6px;
  font-size: 13px; line-height: 1.4; color: var(--muted);
  transition: background-color 150ms var(--ease-base), color 150ms var(--ease-base);
}
.side-sub a:hover { color: var(--ink); background: var(--panel); text-decoration: none; }
.side-sub a[aria-current="true"] { color: var(--accent); font-weight: 600; }
.side-sub .side-sub-2 { list-style: none; margin: 0; padding: 0 0 2px 11px; }
.side-sub .side-sub-2 a { padding: 3px 8px; font-size: 12.5px; }
/* site links live in the drawer on phones only — the top bar hides them there */
.side-site {
  display: none; flex-direction: column; gap: 2px;
  margin-top: 26px; padding-top: 18px; border-top: 1px solid var(--line-soft);
}
.side-site a {
  display: flex; align-items: center; min-height: 40px; padding: 0 10px;
  border-radius: 8px; font-size: 14px; color: var(--ink-soft);
}
.side-site a:hover { background: var(--panel); color: var(--ink); text-decoration: none; }

/* search trigger, at the top of the rail */
.side-search {
  display: flex; align-items: center; gap: 9px; width: 100%;
  height: 40px; margin-bottom: 22px; padding: 0 11px;
  border: 1px solid var(--line); border-radius: 10px;
  background: var(--card); color: var(--muted);
  font-family: inherit; font-size: 14px; cursor: pointer;
  transition: border-color 150ms var(--ease-base), color 150ms var(--ease-base);
}
.side-search svg { width: 15px; height: 15px; flex-shrink: 0; }
.side-search span { flex: 1; text-align: left; }
.side-search kbd { height: 18px; font-size: 10px; padding: 0 5px; border-bottom-width: 1px; }
.side-search kbd + kbd { margin-left: 2px; }
@media (hover: hover) { .side-search:hover { border-color: var(--muted); color: var(--ink); } }

/* --- article ------------------------------------------------------------ */
/* Fills its grid column, so the article's edges line up with the top bar's
   container. No max-width: the container already bounds the measure. */
.doc { min-width: 0; padding: 32px 0 96px; }
/* the skip link and the "Overview" TOC entry target these, not a prose heading */
.doc, .doc-title { scroll-margin-top: calc(var(--nav-h) + 24px); }
.crumbs { display: flex; align-items: center; gap: 6px; font-size: 13px; color: var(--muted); }
.crumbs a { color: var(--muted); }
.crumbs a:hover { color: var(--ink); text-decoration: none; }
.crumbs svg { width: 13px; height: 13px; flex-shrink: 0; opacity: 0.6; }
.crumbs .cur { color: var(--accent); font-weight: 500; }
.doc-head {
  position: relative; isolation: isolate;
  margin: 14px 0 30px; padding-bottom: 22px; border-bottom: 1px solid var(--line-soft);
}
/* the landing page's lavender/rose/sky wash, dialled down to a hint. No
   horizontal bleed: the article now reaches the container edge, so bleeding
   would push the document 2px wider than the viewport on a phone. */
.doc-head::before {
  content: ""; position: absolute; z-index: -1; inset: -20px 0 auto 0; height: 180px;
  background:
    radial-gradient(ellipse 300px 170px at 14% 30%, var(--lav), transparent 70%),
    radial-gradient(ellipse 320px 180px at 66% 4%, var(--sky), transparent 72%),
    radial-gradient(ellipse 260px 160px at 96% 62%, var(--rose), transparent 74%);
  filter: blur(36px); opacity: 0.5; pointer-events: none;
}
@media (prefers-reduced-transparency: reduce) { .doc-head::before { display: none; } }
.doc-title {
  /* margin: 0 — the landing page's h1 rule sets a centred auto margin, which
     would offset the title from the article's left edge */
  margin: 0;
  font-size: clamp(28px, 3.4vw, 36px); line-height: 1.14;
  letter-spacing: -0.026em; font-weight: 700; max-width: 22em;
}
.doc-lead { margin-top: 12px; font-size: 17px; color: var(--ink-soft); }

/* --- prose -------------------------------------------------------------- */
/* the column is the measure: no cap, so tables, code cards and diagrams reach
   the same right edge as the top bar */
.prose { max-width: none; }
.prose p, .prose li { font-size: 16.5px; color: var(--ink-soft); line-height: 1.78; }
.prose p { margin: 16px 0; }
.prose strong { color: var(--ink); font-weight: 650; }
.prose a {
  color: var(--ink); text-decoration: underline;
  text-decoration-color: var(--accent); text-decoration-thickness: 1.5px;
  text-underline-offset: 3.5px;
  transition: opacity 200ms var(--ease-base);
}
.prose a:hover { opacity: 0.8; }
.prose h2 {
  font-size: clamp(23px, 2.6vw, 27px); letter-spacing: -0.02em; line-height: 1.2;
  font-weight: 700; margin: 52px 0 12px; scroll-margin-top: calc(var(--nav-h) + 24px);
}
.prose h3 {
  font-size: 19px; letter-spacing: -0.015em; font-weight: 650;
  margin: 34px 0 8px; scroll-margin-top: calc(var(--nav-h) + 24px);
}
.prose h4 {
  font-size: 16.5px; letter-spacing: -0.01em; font-weight: 650; color: var(--ink);
  margin: 26px 0 6px; scroll-margin-top: calc(var(--nav-h) + 24px);
}
.prose ul, .prose ol { margin: 16px 0; padding-left: 24px; }
.prose li { margin: 6px 0; }
.prose li::marker { color: var(--muted); }
.prose li .codeblock { margin: 12px 0 4px; }
.prose > :first-child { margin-top: 0; }
.prose > :last-child { margin-bottom: 0; }
.h-anchor {
  margin-left: 9px; font-size: 0.62em; font-weight: 600;
  color: var(--muted); text-decoration: none;
}
@media (hover: hover) { .h-anchor { opacity: 0; } .prose :is(h2, h3):hover .h-anchor, .h-anchor:focus-visible { opacity: 1; } }

/* --- on this page, small screens only ----------------------------------- */
/* The rail carries the section list on desktop; this sticky bar is the same
   list for phones, where the rail lives in a drawer. */
.toc-list { position: relative; list-style: none; padding-left: 14px; }
.toc-list::before {
  content: ""; position: absolute; inset-block: 6px; inset-inline-start: 3px;
  width: 1px; background: var(--line);
}
.toc-list a {
  display: block; padding: 5px 0; font-size: 13px; line-height: 1.45;
  color: var(--muted); transition: color 150ms var(--ease-base);
}
.toc-list a:hover { color: var(--ink); text-decoration: none; }
.toc-list a[aria-current="true"] { color: var(--accent); font-weight: 600; }
.toc-list a.sub { padding-left: 12px; font-size: 12.5px; }

/* --- prev / next -------------------------------------------------------- */
.pager { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; margin-top: 64px; }
.pager a {
  display: flex; flex-direction: column; gap: 6px; padding: 14px 18px;
  border: 1px solid var(--line); border-radius: var(--radius);
  background: var(--card); box-shadow: var(--card-shadow); color: var(--ink);
  transition: box-shadow 150ms var(--ease-base), border-color 150ms var(--ease-base);
}
.pager a:hover { text-decoration: none; border-color: var(--muted); }
.pager .dir { display: inline-flex; align-items: center; gap: 6px; font-size: 12.5px; color: var(--muted); }
.pager .dir svg { width: 14px; height: 14px; }
.pager b { font-size: 15px; font-weight: 650; }
.pager a.next { align-items: flex-end; text-align: right; }
.pager a.next .dir { flex-direction: row-reverse; }

/* --- mobile on-this-page bar (shown when the rail is hidden) ------------ */
.xtoc {
  display: none; position: sticky; top: var(--nav-h); z-index: 30;
  border-bottom: 1px solid var(--line-soft);
  background: var(--header-bg); backdrop-filter: blur(12px);
}
.xtoc > summary {
  display: flex; align-items: center; justify-content: space-between; gap: 10px;
  height: 44px; padding: 0 28px; cursor: pointer; list-style: none;
  font-size: 13px; color: var(--ink-soft);
}
.xtoc > summary::-webkit-details-marker { display: none; }
.xtoc .now { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.xtoc .caret { flex-shrink: 0; transition: transform 200ms var(--ease-base); }
.xtoc[open] .caret { transform: rotate(180deg); }
.xtoc-body {
  max-height: 50vh; overflow-y: auto; overscroll-behavior: contain;
  padding: 10px 28px 18px; border-top: 1px solid var(--line-soft);
}

/* cards — linked and informational card grids, in the landing page's language */
.cards {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(13rem, 1fr));
  gap: 16px; margin: 26px 0;
}
.card {
  display: block; background: var(--card); border: 1px solid var(--line);
  border-radius: var(--radius); padding: 20px 22px 22px;
  box-shadow: var(--card-shadow); color: var(--ink); overflow-wrap: break-word;
  transition: transform 160ms var(--ease-base), box-shadow 160ms var(--ease-base),
    border-color 160ms var(--ease-base);
}
.card .label { display: block; margin-bottom: 10px; }
.card h3 { font-size: 17px; letter-spacing: -0.015em; margin: 0 0 6px; font-weight: 650; }
.card p { font-size: 14.5px; color: var(--ink-soft); margin: 0; line-height: 1.65; }
.card h3 a { text-decoration: none; }
a.card { text-decoration: none; }
a.card:hover { text-decoration: none; transform: translateY(-2px); border-color: var(--muted); box-shadow: var(--frame-shadow); }

/* steps — a numbered rail for procedures (code stays inside its step) */
.steps { list-style: none; margin: 26px 0; padding: 0; }
.step {
  position: relative; display: grid; grid-template-columns: 2rem minmax(0, 1fr);
  gap: 14px; padding-bottom: 20px;
}
.step:last-child { padding-bottom: 0; }
.step::before {
  content: ""; position: absolute; left: 0.9375rem; top: 2.25rem; bottom: 0;
  width: 1px; background: var(--line);
}
.step:last-child::before { display: none; }
.step-num {
  position: relative; z-index: 1; width: 2rem; height: 2rem; border-radius: 50%;
  display: inline-flex; align-items: center; justify-content: center;
  background: var(--card); border: 1px solid var(--line);
  font-family: var(--mono); font-size: 12.5px; font-weight: 600; color: var(--ink);
}
.step-body { min-width: 0; padding-top: 0.175rem; }
.step-body p { font-size: 16px; color: var(--ink-soft); line-height: 1.72; margin: 0 0 4px; }
.step-body .codeblock { margin: 14px 0 4px; }
.step-body strong { color: var(--ink); }

/* diagrams — flow chains and layered stacks, drawn with the site's own tokens */
.diagram {
  margin: 26px 0; border: 1px solid var(--line); border-radius: var(--radius);
  background: var(--card); box-shadow: var(--card-shadow); overflow: hidden;
}
.dg-canvas {
  display: flex; flex-direction: column; align-items: center; gap: 0;
  padding: 24px 20px; overflow-x: auto;
  background-image: radial-gradient(var(--line-soft) 1px, transparent 1px);
  background-size: 14px 14px; background-position: -6px -6px;
}
.dg-row {
  display: flex; flex-wrap: wrap; align-items: center; justify-content: center;
  gap: 8px; width: 100%;
}
.dg-row + .dg-row { margin-top: 12px; }
.dg-layer { display: flex; flex-direction: column; align-items: center; }
.dg-node {
  display: inline-flex; flex-direction: column; align-items: flex-start; gap: 2px;
  padding: 8px 14px; border: 1px solid var(--line); border-radius: 10px;
  background: var(--card); box-shadow: 0 1px 2px rgba(23, 24, 28, 0.04);
  font-size: 14px; line-height: 1.35; color: var(--ink); text-align: left;
  max-width: 22rem;
}
[data-theme="dark"] .dg-node { box-shadow: 0 1px 2px rgba(0, 0, 0, 0.4); }
.dg-node .dg-note { font-size: 12px; color: var(--muted); }
.dg-node code { background: none; border: 0; padding: 0; font-size: 0.92em; }
.dg-arrow { width: 26px; height: 10px; flex: none; color: var(--muted); }
.dg-connector {
  width: 1px; height: 22px; background: var(--line);
  position: relative;
}
.dg-connector::after {
  content: ""; position: absolute; left: 50%; bottom: 0; width: 7px; height: 7px;
  border-right: 1px solid var(--muted); border-bottom: 1px solid var(--muted);
  transform: translate(-50%, 1px) rotate(45deg);
}
.dg-children {
  position: relative; display: flex; flex-wrap: nowrap; justify-content: center;
  gap: 12px; margin-top: 16px; padding-top: 16px;
}
/* the fan-out rail: one line across the row, one stub per child. nowrap keeps
   every child under the rail; if the row is wider than the figure, the canvas
   scrolls rather than wrapping the stubs away from it. */
.dg-children::before {
  content: ""; position: absolute; top: 0; left: 10%; right: 10%; height: 1px;
  background: var(--line);
}
.dg-child { position: relative; display: inline-flex; }
.dg-child::before {
  content: ""; position: absolute; top: -16px; left: 50%; width: 1px; height: 16px;
  background: var(--line);
}
.dg-child .dg-node { padding: 7px 12px; font-size: 13.5px; }
.dg-cap {
  padding: 12px 20px 14px; border-top: 1px solid var(--line-soft);
  font-size: 13px; line-height: 1.6; color: var(--muted);
}
.dg-cap b { color: var(--ink-soft); font-weight: 650; }
@media (max-width: 700px) {
  .dg-canvas { padding: 18px 12px; }
  .dg-node { font-size: 13px; padding: 7px 11px; max-width: none; }
  .dg-arrow { width: 18px; }
}

/* tables */
.tbl {
  margin: 26px 0; border: 1px solid var(--line); border-radius: var(--radius);
  overflow: hidden; background: var(--card); box-shadow: var(--card-shadow);
  max-width: 100%;
}
.tbl-scroll { overflow-x: auto; }
.tbl table { width: 100%; border-collapse: collapse; font-size: 14px; }
.tbl thead { background: var(--panel); }
.tbl th {
  text-align: left; font-weight: 600; font-size: 12.5px; letter-spacing: 0.03em;
  text-transform: uppercase; color: var(--muted);
  padding: 12px 18px; border-bottom: 1px solid var(--line); white-space: nowrap;
}
.tbl td { padding: 12px 18px; border-bottom: 1px solid var(--line-soft); vertical-align: top; line-height: 1.6; color: var(--ink-soft); }
/* long identifiers (route paths, tool names) may break, but only when a word
   cannot fit at all — the anywhere value would let columns shrink until words
   split mid-word */
.tbl th, .tbl td { overflow-wrap: break-word; }
.tbl tbody tr:last-child td { border-bottom: none; }

/* code — a card with a header strip, so a block reads as a file, not a wall */
.codeblock {
  margin: 24px 0; border: 1px solid var(--line); border-radius: var(--radius);
  background: var(--card); box-shadow: var(--card-shadow); overflow: hidden;
}
.cb-head {
  display: flex; align-items: center; gap: 8px; min-height: 40px;
  padding: 0 8px 0 16px; border-bottom: 1px solid var(--line-soft); background: var(--panel);
}
.cb-title {
  flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  font-family: var(--mono); font-size: 10.5px; font-weight: 600;
  letter-spacing: 0.12em; text-transform: uppercase; color: var(--muted);
}
.codeblock pre {
  margin: 0; padding: 16px 18px; overflow-x: auto;
  font-size: 13.5px; line-height: 1.72;
  background: none; border: 0; border-radius: 0; box-shadow: none; color: var(--ink);
}
.codeblock code { background: none; padding: 0; font-size: inherit; color: inherit; }
/* whole-line comments dim out; a file tree also dims its connectors + notes */
.t-note { color: var(--muted); }
.t-glyph { color: var(--muted); }
:not(pre) > code {
  background: var(--panel); border: 1px solid var(--line-soft);
  border-radius: 6px; padding: 0.12em 0.42em; font-size: 0.84em; color: var(--ink);
}
.copy {
  flex: none; width: 1.875rem; height: 1.875rem; padding: 0;
  display: inline-flex; align-items: center; justify-content: center;
  border: 1px solid transparent; border-radius: 8px; background: transparent;
  color: var(--muted); cursor: pointer; opacity: 0.6;
  transition: opacity 150ms var(--ease-base), color 150ms var(--ease-base),
    background 150ms var(--ease-base), border-color 150ms var(--ease-base);
}
.codeblock:hover .copy, .copy:focus-visible { opacity: 1; }
.copy:hover { color: var(--ink); background: var(--card); border-color: var(--line); }
/* on touch there is no hover, so the button must be visible at rest */
@media (hover: none) { .copy { opacity: 1; } }
.copy.done { color: var(--c-tip); opacity: 1; }
.copy svg { width: 15px; height: 15px; }
@media (scripting: none) { .copy { display: none; } }

/* method chips — the REST chapter is a long table of method + path cells */
.mchip {
  display: inline-flex; align-items: center; justify-content: center;
  margin-right: 3px; padding: 0.06em 0.42em; border-radius: 5px;
  border: 1px solid currentColor;
  font-family: var(--mono); font-size: 10.5px; font-weight: 700; letter-spacing: 0.04em;
}
.m-get { color: var(--c-tip); }
.m-post { color: var(--c-note); }
.m-put, .m-patch { color: var(--accent); }
.m-delete { color: var(--c-danger); }

/* callouts — a tinted card with an icon badge and a mono label */
.callout {
  --callout: var(--line); --callout-soft: var(--panel);
  display: flex; gap: 14px; margin: 24px 0; padding: 16px 18px;
  border: 1px solid var(--line); border-radius: var(--radius);
  background: var(--card); box-shadow: var(--card-shadow);
}
.callout-icon {
  flex: none; width: 1.625rem; height: 1.625rem; border-radius: 9px;
  display: inline-flex; align-items: center; justify-content: center;
  color: var(--callout); background: var(--callout-soft);
}
.callout-icon svg { width: 15px; height: 15px; }
.callout-body { min-width: 0; }
.callout .label { display: block; margin-bottom: 6px; color: var(--callout); }
.callout p { font-size: 15.5px; color: var(--ink-soft); line-height: 1.7; margin: 0; }
.callout strong { color: var(--ink); }
.callout-note { --callout: var(--c-note); --callout-soft: var(--c-note-soft); }
.callout-tip { --callout: var(--c-tip); --callout-soft: var(--c-tip-soft); }
.callout-warning { --callout: var(--c-warn); --callout-soft: var(--c-warn-soft); }

/* chips — [[Category]] in the source: small, quiet metadata pills */
.chip {
  display: inline-flex; align-items: center; justify-content: center;
  padding: 0.08em 0.46em; border-radius: 999px; vertical-align: middle;
  background: var(--panel); border: 1px solid var(--line);
  font-family: var(--mono); font-size: 0.72em; font-weight: 600;
  letter-spacing: 0.04em; color: var(--ink-soft); white-space: nowrap;
}

/* kbd */
kbd {
  display: inline-flex; align-items: center; justify-content: center;
  height: 22px; padding: 0 7px;
  border: 1px solid var(--line); border-bottom-width: 2px; border-radius: 6px;
  background: var(--card); font-family: var(--mono);
  font-size: 11px; font-weight: 600; color: var(--ink-soft); vertical-align: middle;
}

/* --- mobile sidebar drawer ---------------------------------------------- */
/* fumadocs: overlay fades 300ms ease, panel slides 250ms ease.
   starlight uses a native popover with no animation at all — this is the
   middle ground, and it is guarded by prefers-reduced-motion further down. */
.backdrop {
  position: fixed; inset: var(--nav-h) 0 0; z-index: 44;
  background: rgba(23, 24, 28, 0.4);
  opacity: 0; pointer-events: none; transition: opacity 300ms ease;
}
.backdrop.show { opacity: 1; pointer-events: auto; }
body.drawer-open { overflow: hidden; }

@media (max-width: 900px) {
  .docs { grid-template-columns: minmax(0, 1fr); }
  .xtoc { display: block; }
  .menu-btn { display: inline-flex; }
  .side {
    position: fixed; top: var(--nav-h); bottom: 0; left: 0; z-index: 45;
    width: min(85%, 320px); height: auto; max-height: none;
    padding: 18px 16px 40px 18px; border-right: 1px solid var(--line);
    background: var(--bg); box-shadow: var(--hero-shadow);
    mask-image: none;
    transform: translateX(-100%); transition: transform 250ms ease;
  }
  .side.open { transform: none; }
  .side-site { display: flex; }
  .doc { padding: 24px 0 72px; }
  .pager { grid-template-columns: 1fr; }
  .pager a.next { align-items: flex-start; text-align: left; }
  .pager a.next .dir { flex-direction: row; }
}
@media (min-width: 901px) { .backdrop { display: none !important; } }

/* --- search (Ctrl+K) ---------------------------------------------------- */
.search-overlay {
  position: fixed; inset: 0; z-index: 60;
  background: rgba(23, 24, 28, 0.44); backdrop-filter: blur(4px);
  display: flex; align-items: flex-start; justify-content: center;
  padding: 12vh 20px 20px;
  animation: fade-in 300ms ease;
}
.search-overlay[hidden] { display: none; }
.search-panel {
  width: min(40rem, 100%); overflow: hidden;
  background: var(--card); border: 1px solid var(--line);
  border-radius: var(--radius); box-shadow: var(--hero-shadow);
  animation: dialog-in 300ms var(--ease-dialog);
}
@keyframes fade-in { from { opacity: 0; } }
@keyframes dialog-in { from { opacity: 0; transform: scale(1.03) translateY(-4px); } }
.search-row {
  display: flex; align-items: center; gap: 10px;
  padding: 6px 14px; border-bottom: 1px solid var(--line-soft);
}
.search-row svg { width: 16px; height: 16px; color: var(--muted); flex-shrink: 0; }
.search-row input {
  flex: 1; height: 48px; border: none; background: transparent;
  font-family: inherit; font-size: 16px; color: var(--ink); outline: none;
}
.search-row input::placeholder { color: var(--muted); }
.search-results { max-height: 54vh; overflow-y: auto; padding: 8px; }
.sr-empty { padding: 24px 14px; font-size: 14px; color: var(--muted); text-align: center; }
.sr-item {
  display: flex; align-items: baseline; gap: 10px; width: 100%;
  padding: 10px 12px; border: none; border-radius: 10px;
  background: transparent; text-align: left; cursor: pointer;
  font-family: inherit; color: var(--ink);
}
.sr-item .sr-group {
  font-family: var(--mono); font-size: 10.5px; font-weight: 600;
  letter-spacing: 0.1em; text-transform: uppercase;
  color: var(--muted); flex-shrink: 0;
}
.sr-item .sr-title { font-size: 14.5px; font-weight: 600; white-space: nowrap; }
.sr-item .sr-sec {
  font-size: 13px; color: var(--ink-soft); overflow: hidden;
  text-overflow: ellipsis; white-space: nowrap;
}
.sr-item.active, .sr-item:hover { background: var(--panel); }
.search-foot {
  display: flex; gap: 16px; padding: 10px 14px;
  border-top: 1px solid var(--line-soft); font-size: 12px; color: var(--muted);
}
.search-foot span { display: inline-flex; align-items: center; gap: 4px; }

/* --- skip link (starlight makes this the first focusable element) ------- */
.skip {
  position: fixed; top: 10px; left: 10px; z-index: 70;
  padding: 8px 14px; border-radius: 10px;
  background: var(--btn-bg); color: var(--btn-ink);
  font-size: 14px; font-weight: 600;
  clip-path: inset(50%);
}
.skip:focus { clip-path: none; text-decoration: none; }

/* footer — the site chrome */
footer { border-top: 1px solid var(--line-soft); padding: 40px 0 52px; font-size: 14px; color: var(--muted); background: var(--surface); }
.foot-grid { display: flex; gap: 26px; flex-wrap: wrap; align-items: center; }
.foot-mark { font-weight: 650; font-size: 16.5px; color: var(--ink); display: flex; align-items: center; gap: 10px; }
.foot-mark svg, .foot-mark img { width: 19px; height: 19px; color: var(--ink); }
.foot-links { margin-left: auto; display: flex; gap: 22px; flex-wrap: wrap; }
.foot-links a { color: var(--muted); }
.foot-links a:hover { color: var(--ink); }
.colophon { margin-top: 12px; font-size: 12.5px; color: var(--muted); }

/* Starlight guards 1 of its 4 transitions and fumadocs guards none. Guarding
   everything is a deliberate divergence from both. */
@media (prefers-reduced-motion: reduce) {
  html { scroll-behavior: auto; }
  *, *::before, *::after {
    animation-duration: 0.01ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: 0.01ms !important;
  }
  .reveal { opacity: 1; transform: none; }
  ::view-transition-old(root), ::view-transition-new(root) { animation: none; }
}
@media (prefers-reduced-transparency: reduce) {
  header, .xtoc { background: var(--bg); backdrop-filter: none; }
  .hero-chip { background: var(--card); }
}
@media (prefers-contrast: more) {
  :root { --line: #c9cbd4; --muted: #4a4d58; }
  [data-theme="dark"] { --line: #4a4d58; --muted: #a9adb8; }
  header, .xtoc { background: var(--bg); backdrop-filter: none; border-bottom-color: var(--line); }
}
@media print {
  header, footer, .side, .xtoc, .backdrop,
  .pager, .search-overlay, .skip { display: none !important; }
  .docs { display: block; padding: 0; }
  .hero::before { display: none; }
  .copy { display: none; }
  body { background: #fff; }
}
`;

const THEME_PRE = `document.documentElement.classList.add('js');(function(){try{var s=localStorage.getItem('pmmate-theme');if(s==='dark'||(!s&&window.matchMedia('(prefers-color-scheme: dark)').matches)){document.documentElement.setAttribute('data-theme','dark');}}catch(e){}})();`;

const THEME_JS = `
  /* Theme: system default, manual override persisted. Same shape as the landing
     page's script (commit/apply split, View Transitions when available) — the
     one difference is commit() clears theme-snap synchronously, because a
     throttled rAF would leave the class on <html> and
     \`html.theme-snap * { transition: none !important }\` would then disable
     every transition on the page. */
  (function () {
    var root = document.documentElement;
    var btn = document.getElementById('theme-toggle');
    var stored = null;
    try { stored = localStorage.getItem('pmmate-theme'); } catch (e) {}
    function systemDark() { return window.matchMedia('(prefers-color-scheme: dark)').matches; }
    var transitioning = false;
    function commit(theme) {
      root.classList.add('theme-snap');
      if (theme === 'dark') { root.setAttribute('data-theme', 'dark'); }
      else { root.removeAttribute('data-theme'); }
      if (btn) btn.textContent = theme === 'dark' ? '\\u2600' : '\\u263E';
      void root.offsetHeight;
      root.classList.remove('theme-snap');
    }
    function apply(theme) {
      var reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
      var visible = document.visibilityState === 'visible';
      if (document.startViewTransition && !reduce && visible && !transitioning) {
        transitioning = true;
        try {
          var vt = document.startViewTransition(function () { commit(theme); });
          vt.finished.then(function () { transitioning = false; }, function () { transitioning = false; });
          return;
        } catch (e) {
          transitioning = false;
        }
      }
      commit(theme);
    }
    var current = stored || (systemDark() ? 'dark' : 'light');
    commit(current);
    if (btn) btn.addEventListener('click', function () {
      current = current === 'dark' ? 'light' : 'dark';
      /* assign 'stored' too: otherwise a first visit (storage empty) leaves it
         null and the OS listener below keeps overriding the manual choice */
      stored = current;
      try { localStorage.setItem('pmmate-theme', current); } catch (e) {}
      apply(current);
    });
    window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', function (e) {
      if (!stored) { current = e.matches ? 'dark' : 'light'; apply(current); }
    });
  })();`;

const HEADER_JS = `
  /* the header's bottom edge appears only once content scrolls under it */
  (function () {
    var header = document.querySelector('header');
    if (!header) return;
    var onScroll = function () {
      header.classList.toggle('scrolled', window.scrollY > 4);
    };
    onScroll();
    window.addEventListener('scroll', onScroll, { passive: true });
  })();`;

/* Icons the scripts inject. Kept out of the markup so the pages stay small. */
const ICON_COPY = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect width="14" height="14" x="8" y="8" rx="2"/><path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"/></svg>`;
const ICON_CHECK = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 6 9 17l-5-5"/></svg>`;

const DOCS_JS = `
  /* Sidebar: fold animation + state persistence.
     Fumadocs animates height+opacity for 150ms on cubic-bezier(.45,0,.55,1);
     Starlight's native <details> does not animate at all. This keeps the
     <details> (so it works without JS) and animates the fold on top. */
  (function () {
    var KEY_OPEN = 'mate-docs-groups';
    var KEY_SCROLL = 'mate-docs-side-scroll';
    var EASE = 'cubic-bezier(0.45, 0, 0.55, 1)';
    var DUR = 150;
    var groups = [].slice.call(document.querySelectorAll('.side-group'));
    var stored = null;
    try { stored = JSON.parse(sessionStorage.getItem(KEY_OPEN) || 'null'); } catch (e) {}

    function panelOf(g) { return g.querySelector('ul'); }
    function persist() {
      try {
        var state = {};
        groups.forEach(function (g) { state[g.dataset.group] = g.open; });
        sessionStorage.setItem(KEY_OPEN, JSON.stringify(state));
      } catch (e) {}
    }

    groups.forEach(function (g) {
      var panel = panelOf(g);
      if (!panel) return;
      panel.style.transition = 'height ' + DUR + 'ms ' + EASE + ', opacity ' + DUR + 'ms ' + EASE;
      panel.style.overflow = 'hidden';
      /* restore, then normalise to an explicit height so the fold can animate */
      if (stored && Object.prototype.hasOwnProperty.call(stored, g.dataset.group)) {
        g.open = stored[g.dataset.group];
      }
      panel.style.height = g.open ? '' : '0px';
      panel.style.opacity = g.open ? '' : '0';
      var summary = g.querySelector('summary');
      if (!summary) return;
      summary.addEventListener('click', function (ev) {
        ev.preventDefault();
        /* Target state is explicit: g.open only flips once the fold finishes, so
           finish() and the persisted state must not read it back. And for the
           opening direction scrollHeight has to be measured *after* g.open=true,
           otherwise a closed <details> reports 0 and the panel never expands. */
        var opening = !g.open;
        if (opening) {
          g.open = true;
          panel.style.height = '0px';
          panel.style.opacity = '0';
          void panel.offsetHeight;
          panel.style.height = panel.scrollHeight + 'px';
          panel.style.opacity = '1';
        } else {
          var h = panel.scrollHeight;
          panel.style.height = h + 'px';
          panel.style.opacity = '1';
          void panel.offsetHeight;
          panel.style.height = '0px';
          panel.style.opacity = '0';
        }
        /* <details>/<summary> already expose expanded/collapsed natively — do
           not duplicate it with aria-expanded here. */
        /* One pending finish per panel. Without cancelling the previous listener
           and timer, a rapid double click leaves two closures alive: the first
           still holds opening=true and would yank the panel back to full height
           while the second collapses it. */
        if (panel._te) panel.removeEventListener('transitionend', panel._te);
        if (panel._timer) clearTimeout(panel._timer);
        var done = false;
        function finish() {
          if (done) return;
          done = true;
          panel._te = null;
          panel._timer = null;
          if (opening) { panel.style.height = ''; panel.style.opacity = ''; }
          else { g.open = false; }
          persist();
        }
        panel._te = function (e) {
          if (e.propertyName !== 'height') return;
          panel.removeEventListener('transitionend', panel._te);
          finish();
        };
        panel.addEventListener('transitionend', panel._te);
        /* reduced-motion (or a throttled tab) may never fire transitionend */
        panel._timer = setTimeout(finish, DUR + 60);
      });
    });

    /* the sidebar keeps its scroll position between pages, like starlight's */
    var side = document.getElementById('side');
    if (!side) return;
    var restored = 0;
    try {
      restored = parseFloat(sessionStorage.getItem(KEY_SCROLL) || '0');
      if (restored > 0) side.scrollTop = restored;
    } catch (e) {}
    /* first visit to a deep chapter: bring the current entry into view, so the
       rail never opens on a chapter the reader is not looking at. Deferred to
       the load event because the rail's height (and therefore what counts as
       "in view") is only final once layout has settled. */
    function revealActive() {
      if (side.scrollTop > 0) return;
      var active = side.querySelector('[aria-current="page"]');
      if (active && active.offsetTop > side.clientHeight - 80) {
        side.scrollTop = active.offsetTop - 80;
      }
    }
    if (!(restored > 0)) {
      revealActive();
      window.addEventListener('load', revealActive, { once: true });
    }
    function saveScroll() {
      try { sessionStorage.setItem(KEY_SCROLL, String(side.scrollTop)); } catch (e) {}
    }
    side.addEventListener('scroll', saveScroll);
    window.addEventListener('pagehide', saveScroll);
  })();

  /* Mobile drawer. Fumadocs: 300ms overlay fade + 250ms panel slide.
     Focus moves in, returns to the toggle on close, and Escape closes. */
  (function () {
    var btn = document.getElementById('menu-btn');
    var side = document.getElementById('side');
    var bd = document.getElementById('backdrop');
    if (!btn || !side || !bd) return;
    function set(open) {
      side.classList.toggle('open', open);
      bd.classList.toggle('show', open);
      document.body.classList.toggle('drawer-open', open);
      btn.setAttribute('aria-expanded', String(open));
      if (open) {
        var first = side.querySelector('a');
        if (first) first.focus({ preventScroll: true });
      } else if (document.activeElement && side.contains(document.activeElement)) {
        btn.focus({ preventScroll: true });
      }
    }
    btn.addEventListener('click', function () { set(!side.classList.contains('open')); });
    bd.addEventListener('click', function () { set(false); });
    side.addEventListener('click', function (e) {
      if (e.target.closest && e.target.closest('a')) set(false);
    });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && side.classList.contains('open')) set(false);
    });
    window.matchMedia('(min-width: 901px)').addEventListener('change', function (e) {
      if (e.matches) set(false);
    });
  })();

  /* Scroll spy. The rail and the phone bar share one section list (links carry
     the data-toc attribute), so a single pass highlights both. A heading counts
     as current once it reaches the live header height plus 24px. */
  (function () {
    var links = [].slice.call(document.querySelectorAll('a[data-toc]'));
    /* the page title is included so the top of the page can clear the marker */
    var heads = [].slice.call(
      document.querySelectorAll('.doc-title[id], .prose h2[id], .prose h3[id]')
    );
    var now = document.getElementById('xtoc-now');
    if (!links.length || !heads.length) return;

    function setActive(id) {
      var matched = false;
      links.forEach(function (a) {
        var on = a.getAttribute('href') === '#' + id;
        if (on) {
          a.setAttribute('aria-current', 'true');
          matched = true;
          if (now) now.textContent = a.textContent;
        } else {
          a.removeAttribute('aria-current');
        }
      });
      if (!matched && now) now.textContent = 'On this page';
    }

    function update() {
      var offset = (document.querySelector('header').offsetHeight || 60) + 24;
      var current = heads[0].id;
      for (var i = 0; i < heads.length; i++) {
        if (heads[i].getBoundingClientRect().top <= offset) current = heads[i].id;
        else break;
      }
      setActive(current);
    }

    /* Coalesce with a timer, not requestAnimationFrame: rAF never fires in a
       hidden tab (and is throttled hard in some embedded webviews), which would
       freeze the rail at whatever was current on load. */
    var pending = false;
    function onScroll() {
      if (pending) return;
      pending = true;
      setTimeout(function () { pending = false; update(); }, 16);
    }
    window.addEventListener('scroll', onScroll, { passive: true });
    window.addEventListener('resize', update);
    /* a hidden tab throttles timers, so re-sync the moment it is shown again */
    document.addEventListener('visibilitychange', function () {
      if (document.visibilityState === 'visible') update();
    });
    update();
  })();

  /* Mobile "On this page" bar: closes on pick, and on a click outside. */
  (function () {
    var box = document.getElementById('xtoc');
    if (!box) return;
    box.addEventListener('click', function (e) {
      if (e.target.closest && e.target.closest('a')) box.open = false;
    });
    document.addEventListener('click', function (e) {
      if (box.open && !box.contains(e.target)) box.open = false;
    });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && box.open) { box.open = false; e.target.blur && box.querySelector('summary').focus(); }
    });
  })();

  /* Copy buttons on every code card, in the card's header strip. */
  (function () {
    var COPY = ${JSON.stringify(ICON_COPY)};
    var CHECK = ${JSON.stringify(ICON_CHECK)};
    document.querySelectorAll('.codeblock').forEach(function (wrap) {
      var code = wrap.querySelector('code');
      if (!code) return;
      var btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'copy';
      btn.setAttribute('aria-label', 'Copy code to clipboard');
      btn.innerHTML = COPY;
      btn.addEventListener('click', function () {
        var text = code.innerText;
        function ok() {
          btn.innerHTML = CHECK;
          btn.classList.add('done');
          btn.setAttribute('aria-label', 'Copied');
          setTimeout(function () {
            btn.innerHTML = COPY;
            btn.classList.remove('done');
            btn.setAttribute('aria-label', 'Copy code to clipboard');
          }, 2000);
        }
        function fallback() {
          var ta = document.createElement('textarea');
          ta.value = text;
          ta.setAttribute('readonly', '');
          ta.style.position = 'fixed';
          ta.style.top = '-1000px';
          document.body.appendChild(ta);
          ta.select();
          /* execCommand reports failure by returning false, not by throwing */
          try { if (document.execCommand('copy')) ok(); } catch (e) {}
          document.body.removeChild(ta);
        }
        if (navigator.clipboard && navigator.clipboard.writeText) {
          /* a denied clipboard permission rejects; fall back rather than no-op */
          navigator.clipboard.writeText(text).then(ok, fallback);
        } else {
          fallback();
        }
      });
      wrap.appendChild(btn);
      (wrap.querySelector('.cb-head') || wrap).appendChild(btn);
    });
  })();

  /* Search: a build-time index fetched on first open, so pages stay light. */
  (function () {
    var overlay = document.getElementById('search-overlay');
    var input = document.getElementById('search-input');
    var results = document.getElementById('search-results');
    var openBtn = document.getElementById('search-open');
    if (!overlay || !input || !results) return;
    var INDEX = null, loading = false, active = 0, items = [];

    function esc(s) {
      return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }
    function ensure() {
      if (INDEX || loading) return;
      loading = true;
      fetch('search-index.json').then(function (r) { return r.json(); }).then(function (d) {
        INDEX = d; loading = false; render(search(input.value));
      }).catch(function () { loading = false; });
    }
    function score(entry, words) {
      var t = entry.t.toLowerCase(), g = entry.g.toLowerCase(), total = 0;
      for (var w = 0; w < words.length; w++) {
        var word = words[w], hit = 0;
        if (t.indexOf(word) !== -1) hit += 5;
        if (g.indexOf(word) !== -1) hit += 2;
        if (entry.k.indexOf(word) !== -1) hit += 1;
        for (var s = 0; s < entry.s.length; s++) {
          if (entry.s[s].t.toLowerCase().indexOf(word) !== -1) hit += 3;
        }
        if (!hit) return 0;
        total += hit;
      }
      return total;
    }
    function search(q) {
      if (!INDEX) return [];
      var words = q.trim().toLowerCase().split(' ').filter(Boolean);
      var out = [];
      for (var e = 0; e < INDEX.length; e++) {
        var entry = INDEX[e];
        var sc = words.length ? score(entry, words) : 1;
        if (!sc) continue;
        var sec = null;
        for (var s = 0; !sec && s < entry.s.length; s++) {
          var st = entry.s[s].t.toLowerCase();
          for (var w = 0; w < words.length; w++) {
            if (st.indexOf(words[w]) !== -1) { sec = entry.s[s]; break; }
          }
        }
        out.push({
          href: entry.u + (sec ? '#' + sec.i : ''),
          group: entry.g, title: entry.t, sec: sec ? sec.t : null, score: sc
        });
      }
      out.sort(function (a, b) { return b.score - a.score; });
      return out.slice(0, 12);
    }
    function render(list) {
      items = list; active = 0;
      if (!INDEX) { results.innerHTML = '<div class="sr-empty">Loading&hellip;</div>'; return; }
      if (!list.length) { results.innerHTML = '<div class="sr-empty">No matches.</div>'; return; }
      var html = '';
      for (var i = 0; i < list.length; i++) {
        var r = list[i];
        html += '<button type="button" class="sr-item' + (i === 0 ? ' active' : '') + '" data-href="' + r.href + '">'
          + '<span class="sr-group">' + esc(r.group) + '</span>'
          + '<span class="sr-title">' + esc(r.title) + '</span>'
          + (r.sec ? '<span class="sr-sec">&middot; ' + esc(r.sec) + '</span>' : '')
          + '</button>';
      }
      results.innerHTML = html;
    }
    function open() {
      overlay.hidden = false;
      input.value = '';
      render(search(''));
      ensure();
      /* the panel claims aria-modal, so the rest of the page must not be
         tabbable behind it */
      document.querySelectorAll('header, main, footer, .xtoc, .side').forEach(function (n) {
        n.setAttribute('inert', '');
      });
      input.focus();
    }
    function close() {
      overlay.hidden = true;
      document.querySelectorAll('[inert]').forEach(function (n) { n.removeAttribute('inert'); });
      if (openBtn) openBtn.focus({ preventScroll: true });
    }
    function toggle() { if (overlay.hidden) open(); else close(); }
    function move(d) {
      if (!items.length) return;
      active = (active + d + items.length) % items.length;
      var els = results.querySelectorAll('.sr-item');
      for (var i = 0; i < els.length; i++) els[i].classList.toggle('active', i === active);
      if (els[active] && els[active].scrollIntoView) els[active].scrollIntoView({ block: 'nearest' });
    }
    results.addEventListener('click', function (ev) {
      var b = ev.target.closest ? ev.target.closest('.sr-item') : null;
      if (b) location.href = b.getAttribute('data-href');
    });
    input.addEventListener('input', function () { render(search(input.value)); });
    input.addEventListener('keydown', function (ev) {
      if (ev.key === 'ArrowDown') { move(1); ev.preventDefault(); }
      else if (ev.key === 'ArrowUp') { move(-1); ev.preventDefault(); }
      else if (ev.key === 'Enter' && items.length) { location.href = items[active].href; }
    });
    document.addEventListener('keydown', function (ev) {
      var tag = document.activeElement ? document.activeElement.tagName : '';
      if ((ev.ctrlKey || ev.metaKey) && (ev.key === 'k' || ev.key === 'K')) { ev.preventDefault(); toggle(); }
      else if (ev.key === '/' && tag !== 'INPUT' && tag !== 'TEXTAREA') { ev.preventDefault(); open(); }
      else if (ev.key === 'Escape' && !overlay.hidden) close();
    });
    if (openBtn) openBtn.addEventListener('click', open);
    overlay.addEventListener('mousedown', function (ev) { if (ev.target === overlay) close(); });
  })();`;

/** One row of the "On this page" list. Rendered twice: rail and mobile bar. */
function tocLinks(headings) {
  return [`        <a data-toc href="#_top">Overview</a>`]
    .concat(
      headings.map(
        (h) =>
          `        <a data-toc href="#${h.id}"${h.level === "h3" ? ' class="sub"' : ""}>${esc(
            plainText(h.text)
          )}</a>`
      )
    )
    .join("\n");
}

function layout({ title, desc, main, sidebar, toc }) {
  /* the landing page lives one directory above the docs site */
  const site = "../../index.html";
  const body = `<div class="backdrop" id="backdrop"></div>
${
  toc
    ? `<details class="xtoc" id="xtoc">
  <summary><span class="now" id="xtoc-now">On this page</span>${CARET_ICON}</summary>
  <div class="xtoc-body">
    <div class="toc-list">
${toc}
    </div>
  </div>
</details>
`
    : ""
}<div class="docs">
  <aside class="side" id="side" aria-label="Documentation">
${sidebar}
  </aside>
  <main class="doc" id="main" tabindex="-1">
${main}
  </main>
</div>`;
  return `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>${esc(title)}</title>
<meta name="description" content="${esc(desc)}">
<link rel="icon" href="${ICON}" type="image/svg+xml">
<script>${THEME_PRE}</script>
<style>${CSS}</style>
</head>
<body>
<a class="skip" href="#main">Skip to content</a>
<header>
  <div class="wrap nav">
    <button class="menu-btn" id="menu-btn" aria-label="Open navigation" aria-controls="side" aria-expanded="false">${MENU_ICON}</button>
    <a class="brand" href="${site}">${LOGO} MATE</a>
    <nav class="nav-links">
      <a href="${site}#platform">Platform</a>
      <a href="${site}#community">Community</a>
      <a href="${site}#people">People</a>
      <a class="active" href="introduction.html">Docs</a>
      <a href="https://github.com/Process-Science-Community/MATE">GitHub</a>
      <button class="theme-toggle" id="theme-toggle" aria-label="Toggle color theme">&#9790;</button>
      <a class="btn btn-black" href="https://mate.uni-muenster.de/login">Open MATE</a>
    </nav>
  </div>
</header>
${body}
<footer>
  <div class="wrap">
    <div class="foot-grid">
      <span class="foot-mark">${LOGO} MATE</span>
      <nav class="foot-links">
        <a href="https://mate.uni-muenster.de/login">Open MATE</a>
        <a href="https://github.com/Process-Science-Community/MATE">GitHub</a>
        <a href="introduction.html">Docs</a>
        <a href="${site}#platform">Platform</a>
        <a href="${site}#people">People</a>
      </nav>
    </div>
    <p class="colophon">Built by the community &middot; &copy; 2026 MATE</p>
  </div>
</footer>
<div class="search-overlay" id="search-overlay" hidden>
  <div class="search-panel" role="dialog" aria-modal="true" aria-label="Search documentation">
    <div class="search-row">
      ${SEARCH_ICON}
      <input id="search-input" type="text" placeholder="Search the documentation&hellip;" autocomplete="off" spellcheck="false" />
      <kbd>esc</kbd>
    </div>
    <div class="search-results" id="search-results"></div>
    <div class="search-foot">
      <span><kbd>&uarr;</kbd><kbd>&darr;</kbd> select</span>
      <span><kbd>&crarr;</kbd> open</span>
      <span><kbd>ctrl</kbd><kbd>k</kbd> toggle</span>
    </div>
  </div>
</div>
<script>${THEME_JS}${DOCS_JS}${HEADER_JS}</script>
</body>
</html>
`;
}

/* ---------- pages ---------- */

/** Search trigger, at the top of the rail (above the first group). */
function sideSearchHtml() {
  return `    <button class="side-search" id="search-open" type="button" aria-label="Search the documentation" aria-keyshortcuts="Control+K">${SEARCH_ICON}<span>Search</span><kbd>Ctrl</kbd><kbd>K</kbd></button>`;
}

/** Heading ids in document order — must match renderHeading's slug order. */
function headingIds(blocks) {
  const ids = new Set();
  const list = [];
  for (const b of blocks) {
    if (b.t === "h2" || b.t === "h3") {
      const base = slug(plainText(b.text));
      let id = base;
      let n = 2;
      while (ids.has(id)) id = `${base}-${n++}`;
      ids.add(id);
      list.push({ level: b.t, id, text: b.text });
    }
  }
  return list;
}

/** Chapters in source order, consecutive chapters sharing a group merged. */
function groupChapters(chapters) {
  const groups = [];
  for (const c of chapters) {
    const last = groups[groups.length - 1];
    if (!last || last.name !== c.group) groups.push({ name: c.group, items: [c] });
    else last.items.push(c);
  }
  return groups;
}

/** The left rail. Native <details> so it folds without JS; DOCS_JS animates it.
 *  The rail carries the whole hierarchy — group, chapter, and the current
 *  chapter's sections — because it is the page's only table of contents. */
function sidebarHtml(chapters, current) {
  return groupChapters(chapters)
    .map(
      (g) => `    <details class="side-group" data-group="${esc(g.name)}" open>
      <summary><span>${esc(g.name)}</span>${CARET_ICON}</summary>
      <ul>
${g.items
  .map((c) => {
    const link = `        <li><a class="side-link" href="${c.slug}.html"${
      c === current ? ' aria-current="page"' : ""
    }>${esc(c.title)}</a></li>`;
    if (c !== current) return link;
    const secs = headingIds(c.blocks);
    if (!secs.length) return link;
    /* h2 entries, each carrying its own h3s */
    const items = [];
    for (const h of secs) {
      const a = `<a data-toc href="#${h.id}">${esc(plainText(h.text))}</a>`;
      if (h.level === "h2" || !items.length) items.push({ a, subs: [] });
      else items[items.length - 1].subs.push(a);
    }
    const list = items
      .map(
        (it) =>
          `<li>${it.a}${
            it.subs.length
              ? `<ul class="side-sub-2">${it.subs
                  .map((s) => `<li>${s}</li>`)
                  .join("")}</ul>`
              : ""
          }</li>`
      )
      .join("");
    return `${link}
        <li><ul class="side-sub">${list}</ul></li>`;
  })
  .join("\n")}
      </ul>
    </details>`
    )
    .join("\n");
}

/** The site links Starlight keeps as "mobile preferences" at the foot of the
 *  drawer — the top bar hides them below 820px, so the drawer has to carry them
 *  or Platform/Community/People/GitHub become unreachable on a phone. */
function sideSiteHtml(site) {
  return `    <nav class="side-site" aria-label="Site">
      <a href="${site}#platform">Platform</a>
      <a href="${site}#community">Community</a>
      <a href="${site}#people">People</a>
      <a href="https://github.com/Process-Science-Community/MATE">GitHub</a>
      <a href="https://mate.uni-muenster.de/login">Open MATE</a>
    </nav>`;
}

function crumbsHtml(ch) {
  return `    <nav class="crumbs" aria-label="Breadcrumb">
      <span>${esc(ch.group)}</span>${CHEV_ICON}
      <span class="cur" aria-current="page">${esc(ch.title)}</span>
    </nav>`;
}

function pagerHtml(prev, next) {
  if (!prev && !next) return "";
  const card = (c, dir) =>
    c
      ? `    <a class="${dir}" href="${c.slug}.html" rel="${dir}"><span class="dir">${
          dir === "prev" ? `${PREV_ICON} Previous` : `Next ${NEXT_ICON}`
        }</span><b>${esc(c.title)}</b></a>`
      : `    <span></span>`;
  return `  <nav class="pager" aria-label="Pagination">
${card(prev, "prev")}
${card(next, "next")}
  </nav>`;
}

/** One chapter, in the full docs layout: rail, article, on-this-page. */
function chapterPage(ch, prev, next, chapters) {
  const ids = new Set();
  const { hero, body } = splitChapter(ch.blocks);
  const lead = hero.find((b) => b.t === "p");
  const rest = [...hero.filter((b) => b !== lead), ...body];
  const headings = headingIds(ch.blocks);
  const main = [
    crumbsHtml(ch),
    `    <div class="doc-head">
      <h1 class="doc-title" id="_top">${inline(ch.title)}</h1>${
      lead ? `\n      <p class="doc-lead">${inline(lead.text)}</p>` : ""
    }
    </div>
    <div class="prose">
${rest.map((b) => renderBlock(b, ids)).join("\n")}
    </div>`,
    pagerHtml(prev, next),
  ].join("\n");

  return layout({
    title: `${ch.title} - MATE Docs`,
    desc: snippet(ch),
    sidebar: [
      sideSearchHtml(),
      sidebarHtml(chapters, ch),
      sideSiteHtml("../../index.html"),
    ].join("\n"),
    toc: headings.length ? tocLinks(headings) : "",
    main,
  });
}

/* ---------- build ---------- */

/** Every markdown file under content/, concatenated in filename order. */
function readContent() {
  const files = readdirSync(SRC)
    .filter((f) => f.endsWith(".md"))
    .sort();
  if (!files.length) throw new Error(`no markdown files in ${SRC}`);
  return files.map((f) => readFileSync(join(SRC, f), "utf8")).join("\n\n");
}

/** Every searchable string in a block, directives included. */
function collectWords(b, words) {
  if (b.t === "p" || b.t === "callout") words.push(plainText(b.text));
  else if (b.t === "ul" || b.t === "ol")
    for (const it of b.items) {
      words.push(plainText(it.text));
      if (it.code) words.push(it.code.code);
    }
  else if (b.t === "table") for (const r of b.rows) words.push(plainText(r));
  else if (b.t === "code") words.push(b.title || "", b.code);
  else if (b.t === "directive") for (const x of b.blocks) collectWords(x, words);
}

/** Search index, written once as JSON so no index ships inside every page. */
function buildSearchIndex(chapters) {
  return chapters.map((c) => {
    const secs = headingIds(c.blocks).map((h) => ({ i: h.id, t: plainText(h.text) }));
    const words = [];
    for (const b of c.blocks) collectWords(b, words);
    return {
      u: c.slug + ".html",
      t: c.title,
      g: c.group,
      s: secs,
      k: words.join(" ").toLowerCase().slice(0, 20000),
    };
  });
}

const md = readContent();
const chapters = parseChapters(md);
if (!chapters.length) throw new Error(`no chapters (# H1) found in ${SRC}`);
for (const c of chapters) c.blocks = parseBlocks(c.blocks);

const slugs = new Set();
for (const c of chapters) {
  const base = c.slug || slug(c.title);
  let s = base;
  let n = 2;
  while (slugs.has(s)) s = `${base}-${n++}`;
  slugs.add(s);
  c.slug = s;
}

/* A generated chapter: the whole manual as grouped card grids. It sits in the
   first group, right after the introduction, and has no source of its own. */
rmSync(OUT, { recursive: true, force: true });
mkdirSync(OUT, { recursive: true });

writeFileSync(join(OUT, "search-index.json"), JSON.stringify(buildSearchIndex(chapters)));
const written = ["search-index.json"];
chapters.forEach((c, i) => {
  const file = `${c.slug}.html`;
  writeFileSync(
    join(OUT, file),
    chapterPage(c, chapters[i - 1] || null, chapters[i + 1] || null, chapters)
  );
  written.push(file);
});

console.log(`docs: ${written.length} pages written to ${OUT}`);
for (const c of chapters) console.log(`  ${c.slug}.html  (${c.group} · ${c.title})`);
