"use client";

import Link from "next/link";
import { RotateCw, Wrench } from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/empty-state";
import { PageContainer } from "@/components/page";
import { PanelSkeleton } from "@/components/skeletons";
import { useDevStatus, useReloadModule, type DevModuleStatus } from "@/lib/dev-queries";

/** Lite dev mode overview: every loaded module with its load state, data
 * surface and debug settings, plus links into the Studio. */
export default function DevPage() {
  const { data: status, isLoading, isError } = useDevStatus();

  if (isLoading) return <PanelSkeleton />;
  if (isError || !status) {
    return (
      <EmptyState
        icon={Wrench}
        title="Dev tools are off"
        description="Start the stack with `make lite` (ENV=dev and DEMO_MODE=true) to use the Studio."
      />
    );
  }

  const failed = status.modules.filter((m) => m.load_error).length;

  return (
    <PageContainer className="space-y-6">
      <dl className="grid gap-3 text-sm sm:grid-cols-4">
        <Fact label="Modules" value={`${status.modules.length}${failed ? ` (${failed} failed)` : ""}`} />
        <Fact
          label="MODULES_ONLY"
          value={status.modules_only.length ? status.modules_only.join(", ") : "all"}
        />
        <Fact
          label="debugpy"
          value={status.debugpy_port > 0 ? `127.0.0.1:${status.debugpy_port}` : "off"}
        />
        <Fact label="Worker jobs in API process" value={status.dev_force_thread ? "yes" : "no"} />
      </dl>

      <ul className="space-y-2">
        {status.modules.map((m) => (
          <ModuleRow key={m.id} module={m} />
        ))}
      </ul>
    </PageContainer>
  );
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-border bg-card/40 px-3 py-2">
      <dt className="text-[11px] uppercase tracking-wide text-muted-foreground">{label}</dt>
      <dd className="mt-0.5 truncate font-mono text-xs">{value}</dd>
    </div>
  );
}

function ModuleRow({ module: m }: { module: DevModuleStatus }) {
  const reload = useReloadModule(m.id);
  return (
    <li className="rounded-lg border border-border bg-card/40 p-4">
      <div className="flex flex-wrap items-start gap-3">
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium">
            {m.name} <span className="font-mono text-xs text-muted-foreground">{m.id}</span>
          </p>
          <p className="mt-0.5 truncate font-mono text-[11px] text-muted-foreground">{m.folder}</p>
          <div className="mt-2 flex flex-wrap gap-1.5">
            <Badge variant="outline">{m.isolation}</Badge>
            <Badge variant="outline">{m.execution}</Badge>
            {m.has_panel && <Badge variant="secondary">panel</Badge>}
            {m.has_layout && <Badge variant="secondary">layout.json</Badge>}
          </div>
          <p className="mt-2 text-xs text-muted-foreground">
            Datasets: {m.datasets.length ? m.datasets.join(", ") : "none"} · Widgets:{" "}
            {m.widgets.length ? m.widgets.join(", ") : "none"}
          </p>
          {m.load_error && (
            <pre className="mt-2 max-h-48 overflow-auto whitespace-pre-wrap rounded-md border border-destructive/40 bg-destructive/5 p-2 text-[11px] text-destructive">
              {m.load_error}
            </pre>
          )}
        </div>
        <div className="flex shrink-0 gap-2">
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={reload.isPending}
            onClick={() =>
              reload.mutate(undefined, {
                onSuccess: (s) =>
                  s.load_error
                    ? toast.error(`${m.id} failed to load`)
                    : toast.success(`Reloaded ${m.id}`),
                onError: (e) => toast.error(e.message),
              })
            }
          >
            <RotateCw className="mr-1.5 h-3.5 w-3.5" />
            Reload
          </Button>
          <Button asChild size="sm">
            <Link href={`/dev/studio/${encodeURIComponent(m.id)}`}>Open Studio</Link>
          </Button>
        </div>
      </div>
    </li>
  );
}
