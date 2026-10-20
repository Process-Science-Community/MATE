// Splits a module's `HOW_TO_USE.md` into the pieces the "How to use" panel lays
// out: the lead paragraph, one collapsible block per `##` section, and callouts
// (`> [!NOTE]`, `> [!TIP]`, `> [!WARNING]`) lifted out of the markdown so the
// panel can style them. Pure and DOM-free: the platform guarantees the file's
// shape on upload (`mate.sdk.guide`), but this tolerates a legacy or hand-edited
// file - anything it doesn't recognise simply stays markdown.
//
// Fence-aware throughout: a `## ` or `> [!NOTE]` inside a code block is code.

export type CalloutTone = "note" | "tip" | "warning";

export type GuideBlock =
  | { kind: "markdown"; text: string }
  | { kind: "callout"; tone: CalloutTone; text: string };

export interface GuideSection {
  title: string;
  blocks: GuideBlock[];
}

export interface ParsedGuide {
  /** The `# How to use <name>` title; the panel header already says it. */
  title: string | null;
  lead: GuideBlock[];
  sections: GuideSection[];
}

const FENCE_RE = /^\s*(`{3,}|~{3,})/;
const H1_RE = /^#[ \t]+(\S.*?)[ \t]*$/;
const H2_RE = /^##[ \t]+(\S.*?)[ \t]*$/;
const CALLOUT_RE = /^>[ \t]?\[!(NOTE|TIP|WARNING)\][ \t]*(.*)$/i;

/** Does `line` close the fence opened by `open`? (Same character, at least as long.) */
function closesFence(line: string, open: string): boolean {
  const match = FENCE_RE.exec(line);
  return match !== null && match[1][0] === open[0] && match[1].length >= open.length;
}

/** Group lines into markdown runs and callouts. */
function toBlocks(lines: string[]): GuideBlock[] {
  const blocks: GuideBlock[] = [];
  let buffer: string[] = [];
  const flush = () => {
    const text = buffer.join("\n").trim();
    if (text) blocks.push({ kind: "markdown", text });
    buffer = [];
  };

  let fence: string | null = null;
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (fence !== null) {
      buffer.push(line);
      if (closesFence(line, fence)) fence = null;
      continue;
    }
    const opening = FENCE_RE.exec(line);
    if (opening) {
      fence = opening[1];
      buffer.push(line);
      continue;
    }
    const callout = CALLOUT_RE.exec(line);
    if (callout) {
      // The callout is this line plus every directly following `>` line.
      const quote = [callout[2]];
      let j = i + 1;
      while (j < lines.length && lines[j].startsWith(">")) {
        quote.push(lines[j].replace(/^>[ \t]?/, ""));
        j++;
      }
      flush();
      const text = quote.join("\n").trim();
      if (text) {
        blocks.push({ kind: "callout", tone: callout[1].toLowerCase() as CalloutTone, text });
      }
      i = j - 1;
      continue;
    }
    buffer.push(line);
  }
  flush();
  return blocks;
}

export function parseGuide(markdown: string): ParsedGuide {
  const lines = markdown.replace(/^﻿/, "").replace(/\r\n?/g, "\n").split("\n");

  let title: string | null = null;
  const lead: string[] = [];
  const sections: { title: string; lines: string[] }[] = [];
  let current = lead;

  let fence: string | null = null;
  for (const line of lines) {
    if (fence !== null) {
      current.push(line);
      if (closesFence(line, fence)) fence = null;
      continue;
    }
    const opening = FENCE_RE.exec(line);
    if (opening) {
      fence = opening[1];
      current.push(line);
      continue;
    }
    const h2 = H2_RE.exec(line);
    if (h2) {
      const section = { title: h2[1].replace(/[ \t]+#+$/, ""), lines: [] as string[] };
      sections.push(section);
      current = section.lines;
      continue;
    }
    const h1 = H1_RE.exec(line);
    if (h1 && title === null && sections.length === 0) {
      title = h1[1];
      continue;
    }
    current.push(line);
  }

  return {
    title,
    lead: toBlocks(lead),
    sections: sections.map((s) => ({ title: s.title, blocks: toBlocks(s.lines) })),
  };
}
