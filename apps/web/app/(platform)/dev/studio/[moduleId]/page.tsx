"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useParams, usePathname, useRouter, useSearchParams } from "next/navigation";
import { ExternalLink, Eye, Pencil, RotateCw, Save, Undo2, Wrench } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { EmptyState } from "@/components/empty-state";
import { PageContainer } from "@/components/page";
import { PanelSkeleton } from "@/components/skeletons";
import { ModuleRuntimeWarmup } from "@/components/module-runtime-warmup";
import { CardPalette } from "@/components/dashboards/card-palette";
import { CardInspector } from "@/components/dashboards/card-inspector";
import { type AddStarter } from "@/components/dashboards/dashboard-canvas";
import { ModuleLayoutPage, PANEL_TAB } from "@/components/module-layout/module-layout-page";
import { PageParamsProvider } from "@/lib/module-layout/page-params";
import { useEventLogs, useModules } from "@/lib/queries";
import { useCardCatalog } from "@/lib/dashboard-queries";
import {
  useDevStatus,
  useModuleLayout,
  useReloadModule,
  useSaveModuleLayout,
  type ModuleLayout,
} from "@/lib/dev-queries";
import { PageTabsPopover } from "./page-tabs-popover";

const EMPTY_LAYOUT: ModuleLayout = { version: 1, items: [] };

/** Content-box width of the element the returned callback ref is attached to. */
function useWidth(): [(el: HTMLElement | null) => (() => void) | void, number] {
  const [width, setWidth] = useState(0);
  const ref = useCallback((el: HTMLElement | null) => {
    if (!el) return;
    const ro = new ResizeObserver(([entry]) => {
      if (entry) setWidth(entry.contentRect.width);
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, width];
}

/**
 * Studio: edit a module's `layout.json` on the module page itself. Edit and
 * Preview both render `ModuleLayoutPage`, the module page's own body. In edit
 * mode the grid is laid out at the module page's exact width and scaled to fit
 * between the palette and the inspector, so every card keeps its real shape.
 * Edits stay local until saved.
 */
export default function StudioPage() {
  const { moduleId } = useParams<{ moduleId: string }>();
  const searchParams = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const preview = searchParams.get("mode") === "preview";
  const setQuery = (key: string, value: string | null) => {
    const p = new URLSearchParams(searchParams.toString());
    if (value) p.set(key, value);
    else p.delete(key);
    const qs = p.toString();
    router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
  };
  const setPreview = (on: boolean) => setQuery("mode", on ? "preview" : null);
  const setLogId = (id: string) => setQuery("log", id);

  const { data: status, isLoading: statusLoading } = useDevStatus();
  const layoutQuery = useModuleLayout(moduleId);
  const { data: logs, isLoading: logsLoading } = useEventLogs({ status: "ready" });
  const { data: catalog } = useCardCatalog();
  const save = useSaveModuleLayout(moduleId);
  const reload = useReloadModule(moduleId);

  const [draft, setDraft] = useState<ModuleLayout>(EMPTY_LAYOUT);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [activeTab, setActiveTab] = useState("");
  const startAddRef = useRef<AddStarter | null>(null);

  const saved = layoutQuery.data ?? EMPTY_LAYOUT;
  // Hydrate once: a background refetch (focus, reload) must not drop unsaved edits.
  const hydratedRef = useRef(false);
  useEffect(() => {
    if (hydratedRef.current || layoutQuery.isLoading) return;
    hydratedRef.current = true;
    setDraft(saved);
  }, [layoutQuery.isLoading, saved]);
  // Key order differs between server copies and local edits; compare the content.
  const dirty =
    JSON.stringify([draft.tabs, draft.items]) !== JSON.stringify([saved.tabs, saved.items]);

  // Unsaved edits live only in this component: warn before the tab closes or reloads.
  useEffect(() => {
    if (!dirty) return;
    const warn = (e: BeforeUnloadEvent) => e.preventDefault();
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  const readyLogs = useMemo(() => (logs ?? []).filter((l) => l.status === "ready"), [logs]);
  // `?log=` is the source of truth; a missing or no-longer-ready log falls back to the first.
  const urlLog = searchParams.get("log");
  const logId = readyLogs.some((l) => l.id === urlLog) ? urlLog : (readyLogs[0]?.id ?? null);
  const logModel = readyLogs.find((l) => l.id === logId)?.log_model ?? "case_centric";

  // The module page's view of the module (availability, filter bar, run deps).
  const { data: modules, isLoading: modulesLoading } = useModules(logId);
  const modInfo = modules?.find((m) => m.id === moduleId);

  // Zoom: the module page's grid width (a hidden PageContainer probe, so padding
  // and the 1760px cap match by construction) scaled into the free stage width.
  const [probeRef, pageW] = useWidth();
  const [stageRef, stageW] = useWidth();
  const zoom = pageW > 0 && stageW > 0 ? Math.min(1, stageW / pageW) : 1;
  // Below this the scaled cards are too small to edit (or the stage has no width at all).
  const tooNarrow = pageW > 0 && stageW < 240;

  const mod = status?.modules.find((m) => m.id === moduleId);

  if (statusLoading || layoutQuery.isLoading || logsLoading) return <PanelSkeleton />;
  if (!status) {
    return (
      <EmptyState
        icon={Wrench}
        title="Dev tools are off"
        description="The Studio needs the lite dev stack (`make lite`)."
      />
    );
  }
  if (!mod) {
    return (
      <EmptyState
        icon={Wrench}
        title={`Module "${moduleId}" is not loaded`}
        description="Check MODULES_ONLY and the module's load error on the Dev page."
        primaryAction={
          <Button asChild variant="outline" size="sm">
            <Link href="/dev">Back to Dev</Link>
          </Button>
        }
      />
    );
  }
  if (!logId) {
    return (
      <EmptyState
        icon={Wrench}
        title="No event log to build on"
        description="The Studio previews the module on a ready event log. Upload one first."
        primaryAction={
          <Button asChild variant="outline" size="sm">
            <Link href="/processes">Event logs</Link>
          </Button>
        }
      />
    );
  }

  const body = modulesLoading ? (
    <PanelSkeleton />
  ) : !modInfo ? (
    <p className="py-16 text-center text-sm text-muted-foreground">
      The module list for this log does not include &quot;{moduleId}&quot;.
    </p>
  ) : preview ? (
    // What the module page gets: no layout.json (or an unreadable one) is null there.
    <ModuleLayoutPage
      logId={logId}
      mod={modInfo}
      layout={layoutQuery.data || draft.items.length || draft.tabs?.length ? draft : null}
    />
  ) : (
    <ModuleLayoutPage
      logId={logId}
      mod={modInfo}
      layout={draft}
      edit={{
        items: draft.items,
        onItemsChange: (items) => setDraft((d) => ({ ...d, items })),
        selectedIds,
        onSelectionChange: setSelectedIds,
        startAddRef,
        zoom,
        activeTab,
        onActiveTabChange: (id) => {
          setActiveTab(id);
          setSelectedIds([]);
        },
      }}
    />
  );

  // Both modes return a PageParamsProvider root, so React keeps it (and the
  // page param values) across the Edit/Preview toggle.
  if (preview) {
    // Preview: the exact tree the module page renders, from the unsaved draft.
    return (
      <PageParamsProvider>
        <PageContainer>{body}</PageContainer>
        <Button
          type="button"
          size="sm"
          className="fixed bottom-6 right-6 z-40 rounded-full shadow-lg"
          onClick={() => setPreview(false)}
        >
          <Pencil className="mr-1.5 h-3.5 w-3.5" />
          Edit{dirty ? " (unsaved)" : ""}
        </Button>
      </PageParamsProvider>
    );
  }

  return (
    <PageParamsProvider>
      <div className="relative flex h-full flex-col">
        <ModuleRuntimeWarmup />
        <PageContainer
          aria-hidden
          className="pointer-events-none invisible absolute inset-x-0 top-0 h-0 animate-none py-0"
        >
          <div ref={probeRef} />
        </PageContainer>
        <div className="flex shrink-0 flex-wrap items-center gap-2 border-b border-border px-4 py-2.5 sm:px-6 lg:px-8">
          <span className="text-sm font-medium">{mod.name}</span>
          <span className="font-mono text-xs text-muted-foreground">layout.json</span>
          {layoutQuery.isError && (
            // Saving replaces the unreadable file with what is on the canvas.
            <span className="text-xs text-destructive">{layoutQuery.error.message}</span>
          )}
          <div className="ml-2 flex items-center gap-1.5">
            <span className="text-xs text-muted-foreground">Log</span>
            <Select value={logId} onValueChange={setLogId}>
              <SelectTrigger className="h-8 w-48 text-xs">
                <SelectValue placeholder="Select event log" />
              </SelectTrigger>
              <SelectContent>
                {readyLogs.map((l) => (
                  <SelectItem key={l.id} value={l.id}>
                    {l.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <PageTabsPopover layout={draft} onChange={setDraft} />
          <Button type="button" variant="outline" size="sm" onClick={() => setPreview(true)}>
            <Eye className="mr-1.5 h-3.5 w-3.5" />
            Preview
          </Button>
          <span
            className="font-mono text-xs tabular-nums text-muted-foreground"
            title="The grid is the module page's, scaled to fit"
          >
            {Math.round(zoom * 100)}%
          </span>
          <div className="ml-auto flex items-center gap-2">
            <Button asChild variant="ghost" size="sm">
              <Link
                href={`/processes/${logId}/modules/${encodeURIComponent(moduleId)}`}
                onClick={(e) => {
                  if (dirty && !window.confirm("Leave the Studio and lose your unsaved layout changes?")) {
                    e.preventDefault();
                  }
                }}
              >
                <ExternalLink className="mr-1.5 h-3.5 w-3.5" />
                Module page
              </Link>
            </Button>
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={reload.isPending}
              onClick={() =>
                reload.mutate(undefined, {
                  onSuccess: (s) =>
                    s.load_error
                      ? toast.error(`${moduleId} failed to load: ${s.load_error}`)
                      : toast.success(`Reloaded ${moduleId}`),
                  onError: (e) => toast.error(e.message),
                })
              }
            >
              <RotateCw className="mr-1.5 h-3.5 w-3.5" />
              Reload
            </Button>
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={!dirty}
              onClick={() => {
                setSelectedIds([]);
                setDraft(saved);
              }}
            >
              <Undo2 className="mr-1.5 h-3.5 w-3.5" />
              Discard
            </Button>
            <Button
              type="button"
              size="sm"
              disabled={!dirty || save.isPending}
              onClick={() => {
                const sent = draft;
                save.mutate(sent, {
                  // Adopt the server's normalized copy so the dirty check settles,
                  // unless the user edited on while the request ran.
                  onSuccess: (res) => {
                    setDraft((d) => (d === sent ? res.layout : d));
                    toast.success("Saved layout.json");
                  },
                  onError: (e) => toast.error(e.message),
                });
              }}
            >
              <Save className="mr-1.5 h-3.5 w-3.5" />
              Save
            </Button>
          </div>
        </div>

        {/* Palette and inspector overlay the stage (as in the dashboard editor),
            so the grid width is the module page's, not what is left between them. */}
        <div className="dashboard-canvas-bg relative min-h-0 flex-1">
          {/* The Panel tab and a too-narrow stage mount no canvas to add cards to. */}
          {activeTab !== PANEL_TAB && !tooNarrow && (
            <div className="absolute inset-y-0 left-0 z-20 w-64">
              <CardPalette
                onStartAdd={(req, e) => startAddRef.current?.(req, e)}
                logModel={logModel}
                moduleId={moduleId}
              />
            </div>
          )}
          <div className="absolute inset-y-0 right-0 z-20">
            <CardInspector
              items={draft.items}
              selectedIds={selectedIds}
              catalog={catalog}
              logId={logId}
              onUpdate={(id, patch) =>
                setDraft((d) => ({
                  ...d,
                  items: d.items.map((it) => (it.i === id ? { ...it, ...patch } : it)),
                }))
              }
              onRemove={(ids) => {
                setDraft((d) => ({ ...d, items: d.items.filter((it) => !ids.includes(it.i)) }));
                setSelectedIds([]);
              }}
              onClose={() => setSelectedIds([])}
            />
          </div>
          {/* Inset between the overlays (not padded under them), so its scrollbar
              is not covered by the inspector. */}
          <div
            data-studio-stage
            data-zoom={zoom}
            className="absolute inset-y-0 left-64 right-72 overflow-auto p-3"
          >
            <div ref={stageRef}>
              {tooNarrow ? (
                <p className="py-16 text-center text-sm text-muted-foreground">
                  The window is too narrow to edit the layout. Widen it, or use Preview.
                </p>
              ) : (
                <div className="mx-auto" style={pageW > 0 ? { width: pageW * zoom } : undefined}>
                  {body}
                </div>
              )}
            </div>
          </div>
        </div>
      </div>
    </PageParamsProvider>
  );
}
