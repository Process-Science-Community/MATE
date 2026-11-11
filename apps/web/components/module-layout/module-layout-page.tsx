"use client";

import { useState } from "react";
import Link from "next/link";

import { Button } from "@/components/ui/button";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { DashboardCanvas, type AddStarter } from "@/components/dashboards/dashboard-canvas";
import { LogFilterProvider } from "@/components/processes/log-filter";
import { ModuleRunGate } from "@/components/processes/module-run-gate";
import { PageParamsProvider } from "@/lib/module-layout/page-params";
import { getModulePanel } from "@/lib/module-panels";
import { DEFAULT_CANVAS_SETTINGS, type DashboardItem } from "@/lib/dashboard-queries";
import { useIsDev, type LayoutTab, type ModuleLayout } from "@/lib/dev-queries";
import type { ModuleSummary as ModuleInfo } from "@/lib/api-types";

/** Implicit single tab of a layout without `tabs`, named only when a Panel tab
 * sits next to it. */
const MAIN_TAB: LayoutTab = { id: "main", title: "Overview" };
export const PANEL_TAB = "panel";

const NOOP_ADD_REF = { current: null };
const NOOP = () => {};

export interface ModuleLayoutEdit {
  items: DashboardItem[];
  /** Receives ALL items: the shell merges the active tab's edits and stamps `tab`. */
  onItemsChange: (all: DashboardItem[]) => void;
  selectedIds: string[];
  onSelectionChange: (ids: string[]) => void;
  startAddRef: React.MutableRefObject<AddStarter | null>;
  zoom: number;
  activeTab: string;
  onActiveTabChange: (id: string) => void;
}

/**
 * The module page body: availability states, run gate, log filter bar, page
 * params, tab strip and the layout canvas (or the module's TSX panel). The
 * module page and both Studio modes render exactly this, so what the Studio
 * shows is what the page shows.
 */
export function ModuleLayoutPage({
  logId,
  mod,
  layout,
  edit,
}: {
  logId: string;
  mod: ModuleInfo;
  layout: ModuleLayout | null;
  edit?: ModuleLayoutEdit;
}) {
  // Deep links can reach a module the grid renders grayed-out (disabled, or the
  // log doesn't meet its manifest requirements). Mounting the panel would just
  // fail on its first query - explain instead.
  if (mod.enabled === false) {
    return (
      <div className="rounded-xl border border-dashed border-border bg-card/40 px-6 py-16 text-center">
        <p className="text-sm font-medium">This module is disabled</p>
        <p className="mt-2 text-sm text-muted-foreground">
          Enable it on the{" "}
          <Link href={`/modules/${mod.id}`} className="underline underline-offset-2">
            Modules
          </Link>{" "}
          page to open it.
        </p>
      </div>
    );
  }
  if ((mod.availability?.status ?? "available") === "unavailable") {
    return (
      <div className="rounded-xl border border-dashed border-border bg-card/40 px-6 py-16 text-center">
        <p className="text-sm font-medium">Not available for this process</p>
        <ul className="mt-2 space-y-1 text-sm text-muted-foreground">
          {(mod.availability?.reasons ?? ["This process doesn't meet the module's requirements."]).map(
            (r, i) => (
              <li key={i}>{r}</li>
            ),
          )}
        </ul>
      </div>
    );
  }
  const body = (
    // Log-scoped filter bar; re-scopes every query below through the
    // `X-FF-Event-Filter` header. Modules opt out via `frontend.log_filter`.
    <LogFilterProvider logId={logId} enabled={mod.supports_log_filter}>
      <PageParamsProvider>
        <PageBody logId={logId} mod={mod} layout={layout} edit={edit} />
      </PageParamsProvider>
    </LogFilterProvider>
  );
  // Editing is never gated: a running or failed compute must not lock the
  // layout (data cards still gate themselves through CardRunGate).
  if (edit) return body;
  return (
    // Modules compute lazily: opening one starts its job if the log has no
    // results for it yet, shows live progress, and mounts the body only once
    // they exist. It sits *outside* the filter provider on purpose: the provider
    // owns the body's isolated QueryClient, which is therefore created fresh
    // after each run instead of carrying a stale miss.
    <ModuleRunGate logId={logId} moduleId={mod.id} deps={mod.precompute_deps ?? []}>
      {body}
    </ModuleRunGate>
  );
}

function PageBody({
  logId,
  mod,
  layout,
  edit,
}: {
  logId: string;
  mod: ModuleInfo;
  layout: ModuleLayout | null;
  edit?: ModuleLayoutEdit;
}) {
  const isDev = useIsDev();
  const Panel = getModulePanel(mod.id, { hasFrontend: mod.has_frontend });
  const hasLayout = !!layout || !!edit;
  const pageTabs = !hasLayout ? [] : layout?.tabs?.length ? layout.tabs : [MAIN_TAB];
  const allTabs = Panel ? [...pageTabs, { id: PANEL_TAB, title: "Panel" }] : pageTabs;

  const [ownTab, setOwnTab] = useState<string | null>(null);
  const wanted = edit ? edit.activeTab : ownTab;
  const active = allTabs.some((t) => t.id === wanted) ? wanted! : allTabs[0]?.id;
  const setActive = edit ? edit.onActiveTabChange : setOwnTab;

  if (!active) {
    return (
      <div className="rounded-xl border border-dashed border-border bg-card/40 px-6 py-16 text-center">
        <p className="text-sm text-muted-foreground">
          This module has no frontend panel or layout yet. The platform mounts its API at{" "}
          <code className="rounded bg-muted px-1 text-[11px]">/api/v1/modules/{mod.id}/…</code>.
        </p>
        {isDev && (
          <Button asChild size="sm" variant="outline" className="mt-4">
            <Link href={`/dev/studio/${encodeURIComponent(mod.id)}?log=${encodeURIComponent(logId)}`}>
              Build a layout in the Studio
            </Link>
          </Button>
        )}
      </div>
    );
  }

  const firstTab = pageTabs[0]?.id;
  const hasTabs = !!layout?.tabs?.length;
  const all = edit ? edit.items : (layout?.items ?? []);
  const onTab = (it: DashboardItem) => (it.tab ?? firstTab) === active;

  const body =
    active === PANEL_TAB && Panel ? (
      <Panel logId={logId} moduleId={mod.id} />
    ) : (
      <DashboardCanvas
        items={all.filter(onTab)}
        logId={logId}
        editing={!!edit}
        startAddRef={edit?.startAddRef ?? NOOP_ADD_REF}
        settings={DEFAULT_CANVAS_SETTINGS}
        zoom={edit?.zoom ?? 1}
        selectedIds={edit?.selectedIds}
        onSelectionChange={edit?.onSelectionChange}
        onItemsChange={
          edit
            ? (next) =>
                edit.onItemsChange([
                  ...all.filter((it) => !onTab(it)),
                  // Cards on the first tab keep `tab` unset (the implicit default).
                  ...next.map((it) =>
                    hasTabs && active !== firstTab && it.tab !== active ? { ...it, tab: active } : it,
                  ),
                ])
            : NOOP
        }
      />
    );

  if (allTabs.length < 2) return body;
  return (
    <Tabs value={active} onValueChange={setActive}>
      <TabsList>
        {allTabs.map((t) => (
          <TabsTrigger key={t.id} value={t.id}>
            {t.title}
          </TabsTrigger>
        ))}
      </TabsList>
      {body}
    </Tabs>
  );
}
