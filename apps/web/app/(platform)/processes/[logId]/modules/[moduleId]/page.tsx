"use client";

import { useEffect } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { FileBox } from "lucide-react";

import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/empty-state";
import { PageContainer } from "@/components/page";
import { PanelSkeleton } from "@/components/skeletons";
import { ModuleLayoutPage } from "@/components/module-layout/module-layout-page";
import { useModules } from "@/lib/queries";
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

  const { data: modules, isLoading, isError } = useModules(logId);
  // Fetched in parallel with the module list. A failed fetch (e.g. an API
  // without the endpoint) counts as "no layout", not as an error.
  const layoutQuery = useModuleLayout(moduleId);

  const mod = modules?.find((m) => m.id === moduleId);

  // Same shell as loading.tsx, so the route-level and data-level skeletons are
  // interchangeable - no flash or jump when one hands over to the other.
  if (isLoading || layoutQuery.isLoading) {
    return <PanelSkeleton />;
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
      <ModuleLayoutPage logId={logId} mod={mod} layout={layoutQuery.data ?? null} />
    </PageContainer>
  );
}
