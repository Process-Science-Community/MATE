"use client";

import dynamic from "next/dynamic";
import { usePathname } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { BookOpen, ChevronDown, ChevronsDownUp, ChevronsUpDown, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/cn";
import { parseGuide, type ParsedGuide } from "@/lib/guide-parse";
import { moduleIdFromPath } from "@/lib/page-meta";
import { useModuleGuide, useModules } from "@/lib/queries";
import { useGuidePanel } from "@/lib/stores/guide-panel";
import { useUi } from "@/lib/stores/ui";

// The module "How to use" guide: a topbar button (every module whose manifest
// folder ships a `HOW_TO_USE.md`) and a docked, non-modal panel that renders it.
// Platform-side, like the topbar ⓘ: module authors write markdown, never UI.
//
// Non-modal on purpose - the point is to keep it open while working in the
// module. It sits in the same right-hand column as MATE AI, so the two are
// mutually exclusive (see `useGuidePanel`).

const PANEL_ID = "module-guide-panel";
const TRIGGER_ID = "module-guide-trigger";

// The markdown libraries load the first time a guide is opened, not with the app.
const GuideBlocks = dynamic(() => import("@/components/modules/guide-markdown"), {
  ssr: false,
  loading: () => <GuideSkeleton />,
});

/** Topbar button that toggles the panel; the caller renders it only when the
 *  current module has a guide. */
export function ModuleGuideTopbarButton({ moduleName }: { moduleName: string }) {
  const open = useGuidePanel((s) => s.open);
  const toggle = useGuidePanel((s) => s.toggle);
  return (
    <Button
      id={TRIGGER_ID}
      type="button"
      variant="ghost"
      size="sm"
      className={cn("cursor-pointer gap-1.5 text-muted-foreground", open && "text-foreground")}
      onClick={toggle}
      aria-label={`How to use ${moduleName}`}
      aria-pressed={open}
      aria-controls={PANEL_ID}
    >
      <BookOpen className="h-3.5 w-3.5" />
      <span className="hidden md:inline">How to use</span>
    </Button>
  );
}

export function ModuleGuidePanel() {
  const pathname = usePathname();
  const moduleId = moduleIdFromPath(pathname);
  const { data: modules } = useModules();
  const mod = moduleId ? modules?.find((m) => m.id === moduleId) : undefined;
  const available = mod?.has_guide === true;

  const open = useGuidePanel((s) => s.open);
  const setOpen = useGuidePanel((s) => s.setOpen);
  const mateOpen = useUi((s) => s.mateOpen);
  const visible = open && available;

  // The panel follows the page: it stays open as you move between modules, but a
  // page it can't describe (not a module page, or a module without a guide)
  // resets the toggle so it doesn't pop open later on its own.
  useEffect(() => {
    if (open && modules !== undefined && !available) setOpen(false);
  }, [open, modules, available, setOpen]);

  // MATE AI and the guide share the right-hand column.
  useEffect(() => {
    if (mateOpen && open) setOpen(false);
  }, [mateOpen, open, setOpen]);

  const close = () => {
    setOpen(false);
    document.getElementById(TRIGGER_ID)?.focus();
  };

  const guide = useModuleGuide(visible ? moduleId : null);
  const parsed = useMemo(
    () => (guide.data ? parseGuide(guide.data.markdown) : null),
    [guide.data],
  );

  return (
    <aside
      id={PANEL_ID}
      aria-label={mod ? `How to use ${mod.name}` : "How to use"}
      aria-hidden={!visible}
      inert={!visible}
      onKeyDown={(e) => {
        if (e.key === "Escape") close();
      }}
      className={cn(
        // Docked beside the page from `lg` up; an overlay on narrower screens,
        // where a 440px column would squeeze the module to nothing.
        "flex h-full shrink-0 flex-col overflow-hidden border-l border-white/10 [border-top-color:var(--glass-refraction-top)] bg-sidebar/85 text-sidebar-foreground backdrop-blur-xl backdrop-saturate-150 transition-[width] duration-300 ease-in-out supports-[backdrop-filter]:bg-sidebar/70 motion-reduce:transition-none",
        "max-lg:fixed max-lg:inset-y-0 max-lg:right-0 max-lg:z-40 max-lg:shadow-2xl",
        visible ? "w-[min(440px,100vw)]" : "w-0 border-l-0",
      )}
    >
      <div className="flex h-full w-[min(440px,100vw)] flex-col">
        <PanelHeader name={mod?.name} onClose={close} />
        <div className="min-h-0 flex-1 overflow-y-auto">
          {guide.isPending && visible && <GuideSkeleton />}
          {guide.isError && (
            <div className="space-y-3 p-4 text-sm text-muted-foreground">
              <p>The guide could not be loaded.</p>
              <Button type="button" size="sm" variant="outline" onClick={() => guide.refetch()}>
                Try again
              </Button>
            </div>
          )}
          {parsed && <GuideBody key={guide.data?.module_id} parsed={parsed} />}
        </div>
      </div>
    </aside>
  );
}

function PanelHeader({ name, onClose }: { name: string | undefined; onClose: () => void }) {
  return (
    <header className="flex items-center gap-2.5 border-b border-sidebar-border px-4 py-3">
      <div
        className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-sidebar-primary text-sidebar-primary-foreground"
        aria-hidden
      >
        <BookOpen className="h-4 w-4" />
      </div>
      <div className="min-w-0 flex-1">
        <div className="truncate text-sm font-semibold tracking-tight">How to use</div>
        {name && <div className="truncate text-xs text-sidebar-foreground/60">{name}</div>}
      </div>
      <button
        type="button"
        aria-label="Close the guide"
        onClick={onClose}
        className="flex h-7 w-7 cursor-pointer items-center justify-center rounded-md text-sidebar-foreground/60 transition-colors hover:bg-sidebar-accent hover:text-sidebar-foreground"
      >
        <X className="h-4 w-4" />
      </button>
    </header>
  );
}

/** The lead plus one collapsible block per `##` section. The first section starts
 *  open; the rest are a click away, with one control to open or fold them all. */
function GuideBody({ parsed }: { parsed: ParsedGuide }) {
  const [open, setOpen] = useState<boolean[]>(() => parsed.sections.map((_, i) => i === 0));
  const allOpen = open.length > 0 && open.every(Boolean);

  return (
    <div className="px-4 pb-6 pt-4">
      {parsed.lead.length > 0 && (
        <div className="text-sm text-foreground/90">
          <GuideBlocks blocks={parsed.lead} />
        </div>
      )}
      {parsed.sections.length > 1 && (
        <div className="mt-3 flex justify-end">
          <button
            type="button"
            onClick={() => setOpen(parsed.sections.map(() => !allOpen))}
            className="flex cursor-pointer items-center gap-1 rounded-md px-1.5 py-1 text-[11px] font-medium text-muted-foreground transition-colors hover:bg-sidebar-accent hover:text-foreground"
          >
            {allOpen ? (
              <ChevronsDownUp className="h-3 w-3" aria-hidden />
            ) : (
              <ChevronsUpDown className="h-3 w-3" aria-hidden />
            )}
            {allOpen ? "Collapse all" : "Expand all"}
          </button>
        </div>
      )}
      <div className={cn("border-t", parsed.sections.length > 1 ? "mt-1" : "mt-3")}>
        {parsed.sections.map((section, i) => (
          <details
            key={`${i}-${section.title}`}
            open={open[i] ?? false}
            onToggle={(e) => {
              const next = e.currentTarget.open;
              setOpen((prev) => (prev[i] === next ? prev : prev.map((v, j) => (j === i ? next : v))));
            }}
            className="group border-b"
          >
            <summary className="flex cursor-pointer list-none items-center justify-between gap-2 py-3 text-sm font-medium transition-colors hover:text-primary focus-visible:outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50 [&::-webkit-details-marker]:hidden">
              {section.title}
              <ChevronDown
                className="h-4 w-4 shrink-0 text-muted-foreground transition-transform group-open:rotate-180"
                aria-hidden
              />
            </summary>
            <div className="pb-4 text-[13px] text-foreground/85">
              <GuideBlocks blocks={section.blocks} />
            </div>
          </details>
        ))}
      </div>
    </div>
  );
}

function GuideSkeleton() {
  return (
    <div className="space-y-3 p-4" aria-hidden>
      <Skeleton className="h-4 w-11/12" />
      <Skeleton className="h-4 w-4/5" />
      <Skeleton className="mt-5 h-5 w-2/5" />
      <Skeleton className="h-4 w-full" />
      <Skeleton className="h-4 w-10/12" />
      <Skeleton className="h-4 w-3/4" />
    </div>
  );
}
