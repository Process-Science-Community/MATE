"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { Box, Boxes, Database, Save, Search } from "lucide-react";

import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import type { FlowPaletteModule } from "@/lib/flow-queries";

import { typeColor } from "./flow-graph";

/** What a palette row adds. */
export type FlowAddRequest =
  | { type: "source-artifact" }
  | { type: "module"; module_id: string }
  | { type: "output" };

/**
 * Left rail: sources, the installed modules usable as nodes, and the workspace
 * output. Pressing a row starts a pointer-drag onto the canvas (`onStartAdd`, the
 * dashboard palette's gesture - HTML5 DnD dies behind the prod proxy); Enter/Space
 * on a focused row adds it without a pointer (`onAdd`).
 */
export function FlowPalette({
  modules,
  loading,
  onStartAdd,
  onAdd,
}: {
  modules: FlowPaletteModule[];
  loading: boolean;
  onStartAdd: (req: FlowAddRequest, e: React.PointerEvent) => void;
  onAdd: (req: FlowAddRequest) => void;
}) {
  const [query, setQuery] = useState("");
  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    return modules
      .filter((m) => !q || `${m.name} ${m.module_id} ${m.description ?? ""}`.toLowerCase().includes(q))
      .sort((a, b) => a.name.localeCompare(b.name));
  }, [modules, query]);

  const row = (req: FlowAddRequest, key: string, icon: typeof Box, title: string, sub?: React.ReactNode) => {
    const Icon = icon;
    return (
      <button
        key={key}
        type="button"
        onPointerDown={(e) => {
          if (e.button !== 0) return;
          e.preventDefault();
          onStartAdd(req, e);
        }}
        // A pointer click is handled by the drag gesture (no movement = add); only
        // a keyboard activation (detail 0) adds from here.
        onClick={(e) => e.detail === 0 && onAdd(req)}
        className="flex w-full cursor-grab touch-none items-start gap-2 rounded-md border border-transparent px-2 py-1.5 text-left transition-colors hover:border-border hover:bg-card focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring active:cursor-grabbing"
      >
        <Icon className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
        <span className="min-w-0 flex-1">
          <span className="block truncate text-xs font-medium">{title}</span>
          {sub && <span className="mt-0.5 block text-[11px] leading-snug text-muted-foreground">{sub}</span>}
        </span>
      </button>
    );
  };

  return (
    <div className="flex h-full w-64 shrink-0 flex-col border-r border-border bg-muted/20">
      <div className="border-b border-border p-3">
        <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Nodes</p>
        <p className="mt-0.5 text-[11px] text-muted-foreground">Drag onto the canvas or press Enter.</p>
        <div className="relative mt-2">
          <Search className="pointer-events-none absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search modules…"
            className="h-8 pl-7 text-xs"
            aria-label="Search modules"
          />
        </div>
      </div>
      <ScrollArea className="min-h-0 flex-1 [&_[data-slot=scroll-area-viewport]>div]:!block">
        <div className="space-y-4 p-3">
          <Section title="Sources">
            <div className="flex items-start gap-2 px-2 py-1.5 text-[11px] text-muted-foreground">
              <Database className="mt-0.5 h-4 w-4 shrink-0" />
              The event log is always there: you pick it when you run the flow.
            </div>
            {row({ type: "source-artifact" }, "artifact", Box, "Artifact", "A model or table from your workspace")}
          </Section>
          <Section title="Modules">
            {loading && <p className="px-2 text-xs text-muted-foreground">Loading…</p>}
            {!loading && shown.length === 0 && (
              <p className="px-2 text-xs text-muted-foreground">
                {query ? (
                  "No matches."
                ) : (
                  <>
                    No installed module declares typed outputs for this log type.{" "}
                    <Link href="/modules" className="text-foreground underline underline-offset-2">
                      Browse modules
                    </Link>
                  </>
                )}
              </p>
            )}
            {shown.map((m) =>
              row(
                { type: "module", module_id: m.module_id },
                m.module_id,
                Boxes,
                m.name,
                m.outputs.length > 0 && (
                  <span className="flex flex-wrap gap-1">
                    {m.outputs.map((o) => (
                      <span key={o.dataset_id} className="inline-flex items-center gap-1">
                        <span
                          aria-hidden
                          className="h-1.5 w-1.5 rounded-full"
                          style={{ background: typeColor(o.artifact_type) }}
                        />
                        {o.title || o.dataset_id}
                      </span>
                    ))}
                  </span>
                ),
              ),
            )}
          </Section>
          <Section title="Output">
            {row({ type: "output" }, "output", Save, "Save to workspace", "Names and pins the connected artifact")}
          </Section>
        </div>
      </ScrollArea>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="space-y-1">
      <p className="px-2 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">{title}</p>
      {children}
    </section>
  );
}
