"use client";

import { useMemo, useState } from "react";
import { Boxes, ChevronDown, Play, Search, SlidersHorizontal, Type, type LucideIcon } from "lucide-react";

import { cn } from "@/lib/cn";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { cardIcon } from "@/components/dashboards/card-icon";
import type { AddRequest } from "@/components/dashboards/dashboard-canvas";
import { CopyBlock } from "@/components/dashboards/generic-viz-card";
import {
  CATALOG_GROUPS,
  catalogEntries,
  compatibleDatasets,
  type CatalogEntry,
} from "@/lib/visualizations/catalog";
import {
  ELEMENT_DEFAULTS,
  ELEMENT_KINDS,
  useCardCatalog,
  useDatasetCatalog,
  type DashboardCard,
  type DatasetCatalogEntry,
  type ElementKind,
  type LogModel,
} from "@/lib/dashboard-queries";

const MODEL_LABELS: Record<LogModel, string> = {
  case_centric: "case-centric",
  object_centric: "object-centric (OCEL)",
};

/** Layout-only building blocks, offered only in the module Studio. */
const ELEMENT_META: Record<ElementKind, { icon: LucideIcon; description: string }> = {
  text: { icon: Type, description: "Heading and markdown copy." },
  controls: { icon: SlidersHorizontal, description: "Page parameters or module settings." },
  action: { icon: Play, description: "A button that runs a module POST route." },
};
const ELEMENTS_GROUP = "__elements__";

/** A palette row: a module-authored widget card, or a module dataset rendered
 * through the generic-viz layer. `applicable` is false when the entry's
 * `log_models` don't include the board's model – it is then shown grayed-out
 * (not hidden) with a tooltip explaining why it can't be placed. */
type Entry = (
  | { type: "widget"; key: string; title: string; description: string | null; icon: string | null; card: DashboardCard }
  | {
      type: "viz";
      key: string;
      title: string;
      description: string | null;
      icon: string | null;
      shape: DatasetCatalogEntry["shape"];
      dataset: DatasetCatalogEntry;
    }
  | { type: "element"; key: string; title: string; description: string | null; icon: null; element: ElementKind }
  | {
      type: "catalog";
      key: string;
      title: string;
      description: string | null;
      icon: string;
      entry: CatalogEntry;
      /** Auto-binding: this module's first compatible dataset, or null (placeholder card). */
      dataset: DatasetCatalogEntry | null;
    }
) & { applicable: boolean; logModels: LogModel[] };

type Group = {
  id: string;
  name: string;
  entries: Entry[];
  /** Header icon name; module groups use the generic Boxes glyph. */
  icon?: string;
  /** Viz catalog groups start collapsed (module groups start open). */
  catalog?: boolean;
};

/**
 * Left-rail palette of everything the user's installed modules expose - both
 * widget *cards* and data *datasets* (rendered via the platform's generic
 * visualizations) - grouped per module into collapsible sections. Pressing a
 * row starts a pointer-drag onto the canvas (`onStartAdd`); the canvas tracks
 * the cursor and drops it. A press without dragging adds it at the bottom.
 */
export function CardPalette({
  onStartAdd,
  logModel,
  moduleId,
}: {
  /** Begin a palette→canvas add at the pointer's position. */
  /** `e` null = keyboard add (Enter/Space on a row). */
  onStartAdd: (req: AddRequest, e: React.PointerEvent | null) => void;
  /** Only entries whose `log_models` include the board's model are shown. */
  logModel: LogModel;
  /** Restrict the palette to one module's widgets and datasets (the Studio). */
  moduleId?: string;
}) {
  const { data: cards, isLoading: cardsLoading } = useCardCatalog();
  const { data: datasets, isLoading: datasetsLoading } = useDatasetCatalog();
  const isLoading = cardsLoading || datasetsLoading;
  const [query, setQuery] = useState("");
  // Group ids the user toggled away from their default (module groups open,
  // catalog groups collapsed). A search overrides this so matches are never
  // hidden behind a collapsed header.
  const [toggled, setToggled] = useState<Set<string>>(new Set());

  const groups = useMemo((): Group[] => {
    const q = query.trim().toLowerCase();
    const matches = (title: string, moduleName: string, description: string | null) =>
      !q ||
      title.toLowerCase().includes(q) ||
      moduleName.toLowerCase().includes(q) ||
      (description ?? "").toLowerCase().includes(q);

    const byModule = new Map<string, Group>();
    const ensure = (id: string, name: string) => {
      const g = byModule.get(id) ?? { id, name, entries: [] };
      byModule.set(id, g);
      return g;
    };

    for (const c of cards ?? []) {
      if (moduleId && c.module_id !== moduleId) continue;
      if (!matches(c.title, c.module_name, c.description)) continue;
      ensure(c.module_id, c.module_name).entries.push({
        type: "widget",
        key: `w:${c.module_id}:${c.widget_id}`,
        title: c.title,
        description: c.description,
        icon: c.icon,
        card: c,
        applicable: c.log_models.includes(logModel),
        logModels: c.log_models,
      });
    }
    for (const d of datasets ?? []) {
      if (moduleId && d.module_id !== moduleId) continue;
      if (!matches(d.title, d.module_name, d.description)) continue;
      ensure(d.module_id, d.module_name).entries.push({
        type: "viz",
        key: `d:${d.module_id}:${d.dataset_id}`,
        title: d.title,
        description: d.description,
        icon: d.icon,
        shape: d.shape,
        dataset: d,
        applicable: d.log_models.includes(logModel),
        logModels: d.log_models,
      });
    }
    // Placeable entries first inside each module group; the sort is stable, so
    // the catalog's declared order is kept within each half.
    for (const g of byModule.values()) {
      g.entries.sort((a, b) => Number(b.applicable) - Number(a.applicable));
    }
    const sorted = [...byModule.values()].sort((a, b) => a.name.localeCompare(b.name));
    // Elements only exist in module layouts, so dashboards (no `moduleId`) never see them.
    if (!moduleId) return sorted;
    const elements: Entry[] = ELEMENT_KINDS.filter((k) =>
      matches(ELEMENT_DEFAULTS[k].title, "Elements", ELEMENT_META[k].description),
    ).map((k) => ({
      type: "element",
      key: `e:${k}`,
      title: ELEMENT_DEFAULTS[k].title,
      description: ELEMENT_META[k].description,
      icon: null,
      element: k,
      applicable: true,
      logModels: [],
    }));
    // The viz catalog (Studio only): every viz and preset, auto-bound to this
    // module's first compatible dataset or placed as a "Needs data" card.
    const all = catalogEntries();
    // Until the dataset catalog loads every row would read "needs data" and add an unbound card.
    const catalog: Group[] = datasetsLoading ? [] : CATALOG_GROUPS.map((g) => ({
      id: `__viz_${g.id}__`,
      name: g.label,
      icon: g.icon,
      catalog: true,
      entries: all
        .filter(
          (e) =>
            g.groups.includes(e.spec.group) &&
            matches(e.title, g.label, `${e.description} ${e.key}`),
        )
        .map(
          (e): Entry => ({
            type: "catalog",
            key: `c:${e.key}`,
            title: e.title,
            description: e.description || null,
            icon: e.icon,
            entry: e,
            dataset: compatibleDatasets(e.spec.accepts, datasets, moduleId, logModel, e.key)[0] ?? null,
            applicable: true,
            logModels: [],
          }),
        ),
    })).filter((g) => g.entries.length > 0);
    return [
      ...(elements.length ? [{ id: ELEMENTS_GROUP, name: "Elements", entries: elements }] : []),
      ...sorted,
      ...catalog,
    ];
  }, [cards, datasets, datasetsLoading, query, logModel, moduleId]);

  const searching = query.trim().length > 0;
  const toggle = (id: string) =>
    setToggled((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const requestFor = (entry: Entry): AddRequest =>
    entry.type === "widget"
      ? { kind: "widget", card: entry.card }
      : entry.type === "element"
        ? { kind: "element", element: entry.element, moduleId: moduleId ?? "" }
        : entry.type === "catalog"
          ? {
              kind: "viz",
              dataset: entry.dataset,
              vizId: entry.entry.spec.id,
              options: entry.entry.options,
              mapping: entry.entry.mapping,
              title: entry.dataset?.title ?? entry.title,
              moduleId,
            }
          : { kind: "viz", dataset: entry.dataset };

  return (
    <div className="flex h-full w-64 shrink-0 flex-col border-r border-border bg-muted/20">
      <div className="border-b border-border p-3">
        <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          Cards &amp; data
        </p>
        <p className="mt-0.5 text-[11px] text-muted-foreground">Drag onto the board to add.</p>
        <div className="relative mt-2">
          <Search className="pointer-events-none absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search cards & data…"
            className="h-8 pl-7 text-xs"
          />
        </div>
      </div>

      <ScrollArea className="min-h-0 flex-1 [&_[data-slot=scroll-area-viewport]>div]:!block">
        <div className="space-y-3 p-3">
          {isLoading && <p className="text-xs text-muted-foreground">Loading…</p>}
          {!isLoading && groups.length === 0 && (
            <p className="text-xs text-muted-foreground">
              No matches. Modules expose cards and datasets via their manifest.
            </p>
          )}
          {groups.map((group, gi) => {
            const open =
              searching || (group.catalog ? toggled.has(group.id) : !toggled.has(group.id));
            const GroupIcon = group.icon ? cardIcon(group.icon) : Boxes;
            return (
              <div key={group.id} className="space-y-1.5">
                {group.catalog && !groups[gi - 1]?.catalog && (
                  <p className="px-0.5 pt-1 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
                    Visualizations
                  </p>
                )}
                <section
                  className="overflow-hidden rounded-lg border border-white/10 [border-top-color:var(--glass-refraction-top)] bg-card/40 backdrop-blur-md"
                >
                  <button
                    type="button"
                    onClick={() => toggle(group.id)}
                    aria-expanded={open}
                    className="flex w-full items-center gap-2 bg-muted/40 px-2.5 py-2 text-left hover:bg-muted/60"
                  >
                    <GroupIcon className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                    <span className="min-w-0 flex-1 truncate text-xs font-semibold tracking-tight">
                      {group.name}
                    </span>
                    <span className="shrink-0 rounded-full bg-background/80 px-1.5 text-[10px] font-medium tabular-nums text-muted-foreground">
                      {group.entries.length}
                    </span>
                    <ChevronDown
                      className={cn(
                        "h-3.5 w-3.5 shrink-0 text-muted-foreground transition-transform",
                        !open && "-rotate-90",
                      )}
                    />
                  </button>

                  {open && (
                    <div className="space-y-1 p-1.5">
                      {group.entries.map((entry) => {
                        const Icon =
                          entry.type === "element"
                            ? ELEMENT_META[entry.element].icon
                            : cardIcon(entry.icon);
                        const row = (
                          <div
                            key={entry.key}
                            // Pointer-drag (not HTML5 DnD, which the prod proxy
                            // strips): press to start, the canvas tracks the cursor
                            // and drops. `touch-none` stops the scroll-area from
                            // hijacking the gesture on touch. Inapplicable entries
                            // (wrong log model for this board) don't start a drag.
                            onPointerDown={
                              entry.applicable
                                ? (e) => {
                                    if (e.button !== 0) return;
                                    e.preventDefault();
                                    onStartAdd(requestFor(entry), e);
                                  }
                                : undefined
                            }
                            aria-disabled={!entry.applicable || undefined}
                            role="button"
                            tabIndex={entry.applicable ? 0 : -1}
                            onKeyDown={
                              entry.applicable
                                ? (e) => {
                                    if (e.key !== "Enter" && e.key !== " ") return;
                                    e.preventDefault();
                                    onStartAdd(requestFor(entry), null);
                                  }
                                : undefined
                            }
                            className={cn(
                              "group flex w-full touch-none items-start gap-2 rounded-md border border-transparent px-2 py-1.5 text-left outline-none focus-visible:ring-1 focus-visible:ring-ring",
                              entry.applicable
                                ? "cursor-grab transition-all duration-150 hover:border-border hover:bg-card active:scale-[0.98] active:cursor-grabbing"
                                : "cursor-not-allowed opacity-50",
                            )}
                          >
                            <Icon className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
                            <span className="min-w-0 flex-1">
                              <span className="flex items-center gap-1.5">
                                <span className="min-w-0 flex-1 truncate text-xs font-medium">
                                  {entry.title}
                                </span>
                                {entry.type === "viz" && (
                                  <span className="shrink-0 rounded bg-muted px-1 text-[9px] font-medium uppercase tracking-wide text-muted-foreground">
                                    {entry.shape}
                                  </span>
                                )}
                              </span>
                              {entry.description && (
                                <span className="mt-0.5 line-clamp-2 block text-[11px] leading-snug text-muted-foreground">
                                  {entry.description}
                                </span>
                              )}
                              {entry.type === "catalog" &&
                                (entry.dataset ? (
                                  <span className="mt-0.5 block truncate text-[10px] font-medium text-primary">
                                    → {entry.dataset.title}
                                  </span>
                                ) : (
                                  <span className="mt-0.5 block text-[10px] text-muted-foreground/70">
                                    needs data
                                  </span>
                                ))}
                            </span>
                          </div>
                        );
                        if (entry.applicable) return row;
                        const needs = entry.logModels.map((m) => MODEL_LABELS[m]).join(" or ");
                        return (
                          <Tooltip key={entry.key}>
                            <TooltipTrigger asChild>{row}</TooltipTrigger>
                            <TooltipContent side="right" className="max-w-xs">
                              Needs a {needs} log — this dashboard is bound to a{" "}
                              {MODEL_LABELS[logModel]} log.
                            </TooltipContent>
                          </Tooltip>
                        );
                      })}
                    </div>
                  )}
                </section>
              </div>
            );
          })}
        </div>
      </ScrollArea>
      {moduleId && <WidgetFooter moduleId={moduleId} />}
    </div>
  );
}

/** Studio palette footer: the way out when the catalog has no fitting viz. */
function WidgetFooter({ moduleId }: { moduleId: string }) {
  return (
    <div className="shrink-0 space-y-1.5 border-t border-border p-3 text-[11px] leading-snug text-muted-foreground">
      <p className="font-semibold text-foreground">Need something not listed?</p>
      <p>Code your own widget:</p>
      <CopyBlock text={`make widget ID=${moduleId} NAME=my-widget`} />
      <ol className="list-decimal space-y-0.5 pl-4">
        <li>Run it in the repo root.</li>
        <li>
          Edit <span className="font-mono [overflow-wrap:anywhere]">modules/{moduleId}/widgets/MyWidget.tsx</span>.
        </li>
        <li>Come back to this tab: the widget shows up in the module group above.</li>
      </ol>
      <p>
        Guide: <span className="font-mono [overflow-wrap:anywhere]">docs/DEV_LITE.md → Custom widgets</span>
      </p>
    </div>
  );
}
