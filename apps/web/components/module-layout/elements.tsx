"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import dynamic from "next/dynamic";
import { useQueryClient, type QueryClient } from "@tanstack/react-query";
import { Loader2, Play } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { SchemaForm } from "@/components/schema-form/schema-form";
import type { ConfigSchema, PropSchema } from "@/components/schema-form/types";
import { VizEmpty } from "@/components/visualizations/viz-shell";
import { api } from "@/lib/api";
import { cn } from "@/lib/cn";
import {
  useDatasetCatalog,
  type DashboardItem,
  type DatasetCatalogEntry,
} from "@/lib/dashboard-queries";
import { useDevStatus } from "@/lib/dev-queries";
import { jobProgress } from "@/lib/job-progress";
import { usePageParamsOptional, withQuery } from "@/lib/module-layout/page-params";
import { queryKeys, useModuleConfig, useModuleManifest, useUpdateModuleConfig } from "@/lib/queries";
import { useJobsStore } from "@/lib/stores/jobs";
import { subscribeJob } from "@/lib/ws";

/**
 * Layout-only card kinds (`layout.json`, never dashboards):
 *   - `text`:     markdown copy (`config.markdown`)
 *   - `controls`: page params (`source: "params"`, keys from the module's
 *                 datasets' `params_schema`) or module config (`source: "config"`)
 *   - `action`:   a button that POSTs a module route (`config.route`) with the
 *                 log and the page params, then refreshes the module's cards
 */

// The markdown libraries stay a lazy chunk, as for the module guide.
const Markdown = dynamic(
  () => import("@/components/modules/guide-markdown").then((m) => m.Markdown),
  { loading: () => null },
);

const str = (v: unknown, fallback = ""): string => (typeof v === "string" ? v : fallback);
const strList = (v: unknown): string[] =>
  Array.isArray(v) ? v.filter((k): k is string => typeof k === "string") : [];

/** Every page param the module's datasets declare (first declaration wins). */
function paramsUnion(
  catalog: DatasetCatalogEntry[] | undefined,
  moduleId: string | undefined,
): Record<string, PropSchema> {
  const out: Record<string, PropSchema> = {};
  for (const e of catalog ?? []) {
    if (e.module_id !== moduleId) continue;
    for (const [k, p] of Object.entries(e.params_schema?.properties ?? {})) {
      if (!(k in out)) out[k] = p as PropSchema;
    }
  }
  return out;
}

/** Refresh everything this module's cards cached (same predicate as `CardRunGate`). */
function invalidateModule(qc: QueryClient, moduleId: string) {
  void qc.invalidateQueries({ predicate: (q) => q.queryKey.includes(moduleId) });
}

/** In-flight job per action button, kept outside React state so a remount
 * (tab switch, Edit/Preview) resumes the busy state and the final refresh. */
const runningJobs = new Map<string, string>();

const Hint = ({ children }: { children: string }) => <VizEmpty message={children} />;

// ── Bodies ────────────────────────────────────────────────────────────────

export function ElementBody({
  item,
  logId,
  editing,
}: {
  item: DashboardItem;
  logId: string | null;
  editing: boolean;
}) {
  const cfg = item.config ?? {};
  if (item.kind === "text") {
    const md = str(cfg.markdown);
    if (!md.trim()) return editing ? <Hint>Empty text. Write it in the inspector.</Hint> : null;
    return (
      <div className="h-full overflow-auto text-sm [&>*:first-child]:mt-0 [&>*:last-child]:mb-0">
        <Markdown text={md} />
      </div>
    );
  }
  if (!item.module_id) return <Hint>This element has no module.</Hint>;
  if (item.kind === "controls") {
    const keys = strList(cfg.keys);
    return cfg.source === "config" ? (
      <ConfigControls moduleId={item.module_id} keys={keys} />
    ) : (
      <ParamControls moduleId={item.module_id} keys={keys} />
    );
  }
  if (item.kind === "action") {
    return <ActionButton item={item} moduleId={item.module_id} logId={logId} />;
  }
  return <Hint>Unknown element.</Hint>;
}

function ParamControls({ moduleId, keys }: { moduleId: string; keys: string[] }) {
  const { data: catalog } = useDatasetCatalog();
  const page = usePageParamsOptional();
  const properties = useMemo(() => paramsUnion(catalog, moduleId), [catalog, moduleId]);

  if (!catalog) return null;
  if (Object.keys(properties).length === 0) {
    return <Hint>Nothing to control: declare params_schema on a dataset.</Hint>;
  }
  if (!page) return <Hint>Page controls work on the module page.</Hint>;
  return (
    <SchemaForm
      schema={{ properties }}
      values={page.values}
      onChange={page.set}
      density="compact"
      only={keys.length ? keys : undefined}
      className="h-full overflow-auto"
    />
  );
}

function ConfigControls({ moduleId, keys }: { moduleId: string; keys: string[] }) {
  const manifest = useModuleManifest(moduleId);
  const cfg = useModuleConfig(moduleId);
  const update = useUpdateModuleConfig();
  const qc = useQueryClient();
  const [edits, setEdits] = useState<Record<string, unknown>>({});

  const schema = manifest.data?.config_schema as ConfigSchema | null | undefined;
  if (manifest.isLoading || cfg.isLoading) return null;
  if (!Object.keys(schema?.properties ?? {}).length) {
    return <Hint>This module declares no config_schema.</Hint>;
  }
  const current = cfg.data?.config ?? {};
  const locked = !!(cfg.data?.controlled_by_admin || cfg.data?.controlled_cards?.config);
  const dirty = Object.keys(edits).length > 0;

  const save = () =>
    update.mutate(
      // PUT replaces the whole dict: merge so keys this card doesn't show survive.
      { id: moduleId, config: { ...current, ...edits }, enabled: cfg.data?.enabled ?? true },
      {
        onSuccess: () => {
          // Show the saved values now, not the stale cache until the refetch lands.
          qc.setQueryData(queryKeys.moduleConfig(moduleId), (d: typeof cfg.data) =>
            d && { ...d, config: { ...current, ...edits } },
          );
          setEdits({});
          invalidateModule(qc, moduleId);
        },
        onError: (e) => toast.error(`Could not save settings: ${e.message}`),
      },
    );

  return (
    <div className="flex h-full flex-col gap-2">
      <SchemaForm
        schema={schema}
        values={{ ...current, ...edits }}
        onChange={(k, v) => setEdits((prev) => ({ ...prev, [k]: v }))}
        disabled={locked}
        density="compact"
        only={keys.length ? keys : undefined}
        className="min-h-0 flex-1 overflow-auto"
      />
      <div className="flex justify-end gap-2">
        {dirty && (
          <Button size="sm" variant="ghost" className="h-7 text-xs" onClick={() => setEdits({})}>
            Reset
          </Button>
        )}
        <Button
          size="sm"
          className="h-7 text-xs"
          disabled={!dirty || locked || update.isPending}
          onClick={save}
        >
          {update.isPending && <Loader2 className="h-3 w-3 animate-spin" />}
          Save
        </Button>
      </div>
    </div>
  );
}

function ActionButton({
  item,
  moduleId,
  logId,
}: {
  item: DashboardItem;
  moduleId: string;
  logId: string | null;
}) {
  const route = str(item.config?.route);
  const label = str(item.config?.label) || item.title || "Run";
  const page = usePageParamsOptional();
  const qc = useQueryClient();
  const [starting, setStarting] = useState(false);
  const runKey = `${logId}|${moduleId}|${route}`;
  const [jobId, setJobIdState] = useState<string | null>(() => runningJobs.get(runKey) ?? null);
  const setJobId = useCallback(
    (id: string | null) => {
      if (id) runningJobs.set(runKey, id);
      else runningJobs.delete(runKey);
      setJobIdState(id);
    },
    [runKey],
  );
  const job = useJobsStore((s) => (jobId ? s.byId.get(jobId) : undefined));
  const jobStatus = job?.status;

  // Follow the job to its end, then refresh the module's cards. Failure and
  // cancel toasts are global (jobs provider), so only the refresh happens here.
  // Two sources, both idempotent: the per-job stream can miss a terminal event
  // published between its snapshot and its subscribe; the global store cannot.
  useEffect(() => {
    if (!jobId) return;
    if (jobStatus === "completed") invalidateModule(qc, moduleId);
    if (jobStatus === "completed" || jobStatus === "failed") setJobId(null);
  }, [jobId, jobStatus, qc, moduleId, setJobId]);
  useEffect(() => {
    if (!jobId) return;
    const sub = subscribeJob(jobId, (env) => {
      const status =
        env.topic === "job.snapshot" ? String(env.payload.status ?? "") : env.topic.slice(4);
      if (status === "completed") invalidateModule(qc, moduleId);
      if (status === "completed" || status === "failed" || status === "cancelled") {
        sub.close();
        setJobId(null);
      }
    });
    return () => sub.close();
  }, [jobId, qc, moduleId, setJobId]);

  if (!route) return <Hint>Pick a route in the inspector.</Hint>;

  const run = async () => {
    if (!logId) return;
    setStarting(true);
    try {
      const res = await api<{ job_id?: unknown } | null>(
        withQuery(`/api/v1/modules/${moduleId}${route}`, { log_id: logId, ...page?.values }),
        { method: "POST" },
      );
      if (res && typeof res.job_id === "string") setJobId(res.job_id);
      else invalidateModule(qc, moduleId);
    } catch (e) {
      toast.error(`${label} failed: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setStarting(false);
    }
  };

  const busy = starting || jobId !== null;
  const progress = job ? jobProgress(job) : null;
  const progressText = progress?.pct != null ? progress.label : "Working…";
  // Progress lives inside the button: the default action card is only as tall
  // as the button, so a bar below it would be clipped by the card.
  return (
    <div className="flex h-full flex-col items-stretch justify-center">
      <Button
        onClick={run}
        disabled={busy || !logId}
        title={logId ? undefined : "Select an event log first"}
        className="relative w-full overflow-hidden"
      >
        {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
        <span className="truncate">{label}</span>
        {jobId && (
          <>
            <span className="shrink-0 tabular-nums opacity-80">{progressText}</span>
            <span
              aria-hidden
              className={cn(
                "absolute inset-x-0 bottom-0 h-0.5 origin-left bg-primary-foreground/70 transition-transform duration-300",
                progress?.pct == null && "animate-pulse",
              )}
              style={{ transform: `scaleX(${(progress?.pct ?? 100) / 100})` }}
            />
          </>
        )}
      </Button>
      {jobId && (
        <span role="status" aria-live="polite" className="sr-only">
          {label}: {progressText}
        </span>
      )}
    </div>
  );
}

// ── Inspector ─────────────────────────────────────────────────────────────

export function ElementSettings({
  item,
  onChange,
}: {
  item: DashboardItem;
  onChange: (patch: { title?: string | null; config: Record<string, unknown> }) => void;
}) {
  const cfg = item.config ?? {};
  const setConfig = (key: string, value: unknown) => onChange({ config: { ...cfg, [key]: value } });

  return (
    <div className="space-y-3.5">
      <div className="space-y-1.5">
        <Label htmlFor="element-title" className="text-xs">
          Title
        </Label>
        <Input
          id="element-title"
          value={item.title ?? ""}
          onChange={(e) => onChange({ title: e.target.value, config: cfg })}
          className="h-8 text-xs"
        />
      </div>
      {item.kind === "text" && (
        <div className="space-y-1.5">
          <Label htmlFor="element-markdown" className="text-xs">
            Markdown
          </Label>
          <textarea
            id="element-markdown"
            value={str(cfg.markdown)}
            onChange={(e) => setConfig("markdown", e.target.value)}
            rows={8}
            className="w-full rounded-md border border-input bg-transparent px-2.5 py-1.5 font-mono text-xs shadow-xs outline-none focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 dark:bg-input/30"
          />
        </div>
      )}
      {item.kind === "controls" && item.module_id && (
        <ControlsSettings moduleId={item.module_id} cfg={cfg} onChange={onChange} />
      )}
      {item.kind === "action" && item.module_id && (
        <ActionSettings moduleId={item.module_id} cfg={cfg} setConfig={setConfig} />
      )}
    </div>
  );
}

function ControlsSettings({
  moduleId,
  cfg,
  onChange,
}: {
  moduleId: string;
  cfg: Record<string, unknown>;
  onChange: (patch: { config: Record<string, unknown> }) => void;
}) {
  const { data: catalog } = useDatasetCatalog();
  const manifest = useModuleManifest(moduleId);
  const source = cfg.source === "config" ? "config" : "params";
  const available =
    source === "params"
      ? paramsUnion(catalog, moduleId)
      : ((manifest.data?.config_schema as ConfigSchema | null | undefined)?.properties ?? {});
  const keys = Object.keys(available);

  const schema: ConfigSchema = {
    properties: {
      source: {
        type: "string",
        title: "Source",
        enum: ["params", "config"],
        enumLabels: ["Page params", "Module config"],
        ui: { widget: "segmented" },
      },
      keys: {
        type: "array",
        title: "Fields",
        description: keys.length
          ? "None selected shows all."
          : source === "params"
            ? "No dataset declares params_schema."
            : "The module declares no config_schema.",
        enum: keys,
        enumLabels: keys.map((k) => available[k]?.title ?? k),
        ui: { widget: "multiselect" },
      },
    },
  };
  return (
    <SchemaForm
      schema={schema}
      values={{ source, keys: strList(cfg.keys) }}
      // A new source has other keys: start from "all".
      onChange={(k, v) =>
        onChange({ config: k === "source" ? { ...cfg, source: v, keys: [] } : { ...cfg, [k]: v } })
      }
      density="compact"
    />
  );
}

function ActionSettings({
  moduleId,
  cfg,
  setConfig,
}: {
  moduleId: string;
  cfg: Record<string, unknown>;
  setConfig: (key: string, value: unknown) => void;
}) {
  const { data: dev } = useDevStatus();
  const routes = dev?.modules.find((m) => m.id === moduleId)?.routes?.filter((r) => r.method === "POST") ?? [];
  const current = str(cfg.route);
  // Keep a saved route selectable even when dev status doesn't list it.
  const options = routes.map((r) => ({ path: r.path, label: r.job ? `${r.path} (job)` : r.path }));
  if (current && !options.some((o) => o.path === current)) options.push({ path: current, label: current });

  const schema: ConfigSchema = {
    properties: {
      route: {
        type: "string",
        title: "Route",
        description: options.length ? "POST route of this module." : "The module has no POST routes.",
        enum: options.map((o) => o.path),
        enumLabels: options.map((o) => o.label),
        ui: { widget: "select" },
      },
      label: { type: "string", title: "Button label", default: "Run" },
    },
  };
  return (
    <SchemaForm
      schema={schema}
      values={{ route: current, label: cfg.label }}
      onChange={setConfig}
      density="compact"
    />
  );
}
