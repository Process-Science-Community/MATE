"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { ExternalLink, RotateCw, Save, Undo2, Wrench } from "lucide-react";
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
import { PanelSkeleton } from "@/components/skeletons";
import { ModuleRuntimeWarmup } from "@/components/module-runtime-warmup";
import { CardPalette } from "@/components/dashboards/card-palette";
import { CardInspector } from "@/components/dashboards/card-inspector";
import { DashboardCanvas, type AddStarter } from "@/components/dashboards/dashboard-canvas";
import { useEventLogs } from "@/lib/queries";
import {
  DEFAULT_CANVAS_SETTINGS,
  useCardCatalog,
  type DashboardItem,
} from "@/lib/dashboard-queries";
import {
  useDevStatus,
  useModuleLayout,
  useReloadModule,
  useSaveModuleLayout,
} from "@/lib/dev-queries";

/**
 * Studio: edit a module's `layout.json` with the dashboard editor. The palette
 * only offers this module's datasets and widgets; Save writes the file into the
 * module folder through the dev API. Edits stay local until saved.
 */
export default function StudioPage() {
  const { moduleId } = useParams<{ moduleId: string }>();
  const searchParams = useSearchParams();
  const { data: status, isLoading: statusLoading } = useDevStatus();
  const layoutQuery = useModuleLayout(moduleId);
  const { data: logs } = useEventLogs({ status: "ready" });
  const { data: catalog } = useCardCatalog();
  const save = useSaveModuleLayout(moduleId);
  const reload = useReloadModule(moduleId);

  const [items, setItems] = useState<DashboardItem[]>([]);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [logId, setLogId] = useState<string | null>(searchParams.get("log"));
  const startAddRef = useRef<AddStarter | null>(null);

  const savedItems = useMemo(() => layoutQuery.data?.items ?? [], [layoutQuery.data]);
  // Hydrate once: a background refetch (focus, reload) must not drop unsaved edits.
  const hydratedRef = useRef(false);
  useEffect(() => {
    if (hydratedRef.current || layoutQuery.isLoading) return;
    hydratedRef.current = true;
    setItems(savedItems);
  }, [layoutQuery.isLoading, savedItems]);
  const dirty = JSON.stringify(items) !== JSON.stringify(savedItems);

  const readyLogs = useMemo(() => (logs ?? []).filter((l) => l.status === "ready"), [logs]);
  useEffect(() => {
    if (!logId && readyLogs.length) setLogId(readyLogs[0].id);
  }, [logId, readyLogs]);
  const logModel = readyLogs.find((l) => l.id === logId)?.log_model ?? "case_centric";

  const mod = status?.modules.find((m) => m.id === moduleId);

  if (statusLoading || layoutQuery.isLoading) return <PanelSkeleton />;
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

  return (
    <div className="flex h-full flex-col">
      <ModuleRuntimeWarmup />
      <div className="flex shrink-0 flex-wrap items-center gap-2 border-b border-border px-4 py-2.5 sm:px-6 lg:px-8">
        <span className="text-sm font-medium">{mod.name}</span>
        <span className="font-mono text-xs text-muted-foreground">layout.json</span>
        {layoutQuery.isError && (
          // Saving replaces the unreadable file with what is on the canvas.
          <span className="text-xs text-destructive">{layoutQuery.error.message}</span>
        )}
        <div className="ml-2 flex items-center gap-1.5">
          <span className="text-xs text-muted-foreground">Log</span>
          <Select value={logId ?? ""} onValueChange={setLogId}>
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
        <div className="ml-auto flex items-center gap-2">
          {logId && (
            <Button asChild variant="ghost" size="sm">
              <Link href={`/processes/${logId}/modules/${encodeURIComponent(moduleId)}`}>
                <ExternalLink className="mr-1.5 h-3.5 w-3.5" />
                Module page
              </Link>
            </Button>
          )}
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
            Reload module
          </Button>
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={!dirty}
            onClick={() => {
              setSelectedIds([]);
              setItems(savedItems);
            }}
          >
            <Undo2 className="mr-1.5 h-3.5 w-3.5" />
            Discard
          </Button>
          <Button
            type="button"
            size="sm"
            disabled={!dirty || save.isPending}
            onClick={() =>
              save.mutate(items, {
                // Adopt the server's normalized copy so the dirty check settles.
                onSuccess: (res) => {
                  setItems(res.layout.items);
                  toast.success("Saved layout.json");
                },
                onError: (e) => toast.error(e.message),
              })
            }
          >
            <Save className="mr-1.5 h-3.5 w-3.5" />
            Save
          </Button>
        </div>
      </div>

      <div className="flex min-h-0 flex-1">
        <CardPalette
          onStartAdd={(req, e) => startAddRef.current?.(req, e)}
          logModel={logModel}
          moduleId={moduleId}
        />
        <div className="dashboard-canvas-bg relative h-full min-w-0 flex-1 overflow-auto p-3">
          <DashboardCanvas
            items={items}
            logId={logId}
            editing
            startAddRef={startAddRef}
            settings={DEFAULT_CANVAS_SETTINGS}
            selectedIds={selectedIds}
            onSelectionChange={setSelectedIds}
            onItemsChange={(next) => setItems(next)}
          />
        </div>
        <CardInspector
          items={items}
          selectedIds={selectedIds}
          catalog={catalog}
          logId={logId}
          onUpdate={(id, patch) =>
            setItems((cur) => cur.map((it) => (it.i === id ? { ...it, ...patch } : it)))
          }
          onRemove={(ids) => {
            setItems((cur) => cur.filter((it) => !ids.includes(it.i)));
            setSelectedIds([]);
          }}
          onClose={() => setSelectedIds([])}
        />
      </div>
    </div>
  );
}
