"use client";

import type { ReactNode } from "react";
import { Info, Lightbulb, TriangleAlert, type LucideIcon } from "lucide-react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

import { cn } from "@/lib/cn";
import type { CalloutTone, GuideBlock } from "@/lib/guide-parse";

// Renders the markdown of a module's `HOW_TO_USE.md` for the "How to use" panel.
// Lives apart from the panel so the markdown libraries are one lazy chunk: they
// load the first time a guide is opened, not with the app shell.
//
// Safe by construction: react-markdown never renders raw HTML (no rehype-raw)
// and rewrites `javascript:` URLs. The platform also guarantees the format on
// upload (`mate.sdk.guide`: absolute links, no images, no HTML), so the `img`
// mapping below only matters for a legacy file.

const COMPONENTS: Components = {
  h1: ({ children }) => <SubHeading>{children}</SubHeading>,
  h2: ({ children }) => <SubHeading>{children}</SubHeading>,
  h3: ({ children }) => <SubHeading>{children}</SubHeading>,
  h4: ({ children }) => (
    <h4 className="mt-3 mb-1 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
      {children}
    </h4>
  ),
  p: ({ children }) => <p className="my-2 leading-relaxed">{children}</p>,
  strong: ({ children }) => <strong className="font-semibold text-foreground">{children}</strong>,
  em: ({ children }) => <em className="italic">{children}</em>,
  ul: ({ children }) => <ul className="my-2 list-disc space-y-1 pl-5">{children}</ul>,
  ol: ({ children }) => <ol className="my-2 list-decimal space-y-1 pl-5">{children}</ol>,
  li: ({ children }) => <li className="leading-relaxed marker:text-muted-foreground">{children}</li>,
  a: ({ href, children }) => (
    <a
      href={href}
      target="_blank"
      rel="noreferrer"
      className="font-medium text-primary underline-offset-2 hover:underline"
    >
      {children}
    </a>
  ),
  code: ({ children }) => (
    <code className="rounded bg-muted px-1 py-0.5 font-mono text-[12px] text-foreground">
      {children}
    </code>
  ),
  pre: ({ children }) => (
    <pre className="my-3 overflow-x-auto rounded-md border bg-muted/40 p-3 font-mono text-[12px] leading-relaxed [&>code]:bg-transparent [&>code]:p-0">
      {children}
    </pre>
  ),
  blockquote: ({ children }) => (
    <blockquote className="my-3 border-l-2 pl-3 text-muted-foreground">{children}</blockquote>
  ),
  table: ({ children }) => (
    <div className="my-3 overflow-x-auto rounded-md border">
      <table className="w-full border-collapse text-left text-xs">{children}</table>
    </div>
  ),
  th: ({ children }) => (
    <th className="border-b bg-muted/40 px-2.5 py-1.5 font-medium text-foreground">{children}</th>
  ),
  td: ({ children }) => (
    <td className="border-b px-2.5 py-1.5 align-top leading-relaxed [tr:last-child_&]:border-b-0">
      {children}
    </td>
  ),
  hr: () => <hr className="my-4 border-border" />,
  img: () => null,
};

function SubHeading({ children }: { children?: ReactNode }) {
  return <h3 className="mt-4 mb-1 text-[13px] font-semibold text-foreground">{children}</h3>;
}

const TONES: Record<CalloutTone, { label: string; icon: LucideIcon; className: string }> = {
  note: { label: "Note", icon: Info, className: "border-primary/30 bg-primary/5" },
  tip: { label: "Tip", icon: Lightbulb, className: "border-border bg-muted/40" },
  warning: {
    label: "Warning",
    icon: TriangleAlert,
    className: "border-destructive/40 bg-destructive/5",
  },
};

function Markdown({ text }: { text: string }) {
  return (
    <ReactMarkdown remarkPlugins={[remarkGfm]} components={COMPONENTS}>
      {text}
    </ReactMarkdown>
  );
}

function Callout({ tone, text }: { tone: CalloutTone; text: string }) {
  const { label, icon: Icon, className } = TONES[tone];
  return (
    <div role="note" className={cn("my-3 rounded-md border px-3 py-2.5", className)}>
      <div className="mb-0.5 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide text-foreground">
        <Icon className="h-3 w-3 shrink-0" aria-hidden />
        {label}
      </div>
      <div className="[&>*:first-child]:mt-0 [&>*:last-child]:mb-0">
        <Markdown text={text} />
      </div>
    </div>
  );
}

/** One part of a guide (the lead, or the body of a `##` section). */
export default function GuideBlocks({ blocks }: { blocks: GuideBlock[] }) {
  return (
    <div className="[&>*:first-child]:mt-0 [&>*:last-child]:mb-0">
      {blocks.map((block, i) =>
        block.kind === "callout" ? (
          <Callout key={i} tone={block.tone} text={block.text} />
        ) : (
          <Markdown key={i} text={block.text} />
        ),
      )}
    </div>
  );
}
