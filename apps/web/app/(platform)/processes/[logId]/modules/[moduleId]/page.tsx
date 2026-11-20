"use client";

import { useEffect } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { Ban, FileBox } from "lucide-react";

import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/empty-state";
import { PageContainer } from "@/components/page";
import { PanelSkeleton } from "@/components/skeletons";
import { ModuleLayoutPage } from "@/components/module-layout/module-layout-page";
import { DatasetLineageBanner, isWithdrawn } from "@/components/datasets/dataset-lineage-banner";
import { useEventLog, useModules } from "@/lib/queries";
import { prefetchModulePanel } from "@/lib/module-panels";
import { useModuleLayout } from "@/lib/dev-queries";

export default function ModulePage() {
  const params = useParams<{ logId: string; moduleId: string }>();
  const { logId, moduleId } = params;

  // Start the panel bundle + runtime on mount rather than after `useModules`
  // resolves. The route already knows which module it is, so gating the two
  // large transfers behind the module-list round trip only serialised them.
  // A deep link or reload (no hover prefetch from the grid) used to pay all
  // three back to back. Rendering still waits for the query below - this only
  // moves the fetch off the critical path, it does not mount anything early.
  useEffect(() => {
    prefetchModulePanel(moduleId);
  }, [moduleId]);

  const { data: modules, isLoading, isError, error } = useModules(logId);
  // Fetched in parallel with the module list. A failed fetch (e.g. an API
  // without the endpoint) counts as "no layout", not as an error.
  const layoutQuery = useModuleLayout(moduleId);

  // Cached from the process page or the processes list in the usual flow; on a
  // deep link it is fetched alongside the module list.
  const { data: log } = useEventLog(logId);

  const mod = modules?.find((m) => m.id === moduleId);

  // A copy of a withdrawn dataset: the module routes answer 410, so show the
  // withdrawn panel (reason + "Delete my copy") instead of a loader error.
  if (log && isWithdrawn(log.source_dataset)) {
    return (
      <PageContainer>
        <DatasetLineageBanner log={log} />
      </PageContainer>
    );
  }

  // Same shell as loading.tsx, so the route-level and data-level skeletons are
  // interchangeable - no flash or jump when one hands over to the other.
  if (isLoading || layoutQuery.isLoading) {
    return <PanelSkeleton />;
  }
  if (isError && (error as { status?: number } | null)?.status === 410) {
    // Withdrawn while the page was open: the cached log still says "active"
    // until its refetch (triggered by the 410 in `lib/api.ts`) lands.
    return (
      <EmptyState
        icon={Ban}
        title="This dataset has been withdrawn"
        description="An administrator withdrew the dataset this log was copied from."
      />
    );
  }
  if (isError) {
    return (
      <EmptyState
        icon={FileBox}
        title="Couldn't load module"
        description="The module loader is offline or failed to start."
      />
    );
  }
  if (!mod) {
    return (
      <EmptyState
        icon={FileBox}
        title={`Module "${moduleId}" not found`}
        description="It may not be installed or may have failed to load."
        primaryAction={
          <Button asChild className="cursor-pointer">
            <Link href="/modules/import">Install a module</Link>
          </Button>
        }
      />
    );
  }

  return (
    <PageContainer>
      {log && <DatasetLineageBanner log={log} />}
      <ModuleLayoutPage logId={logId} mod={mod} layout={layoutQuery.data ?? null} />
    </PageContainer>
  );
}
