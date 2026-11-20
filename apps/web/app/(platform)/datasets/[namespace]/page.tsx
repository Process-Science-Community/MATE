import { Suspense } from "react";

import { CardGridSkeleton, PageSkeleton } from "@/components/skeletons";
import { DatasetsClient } from "../datasets-client";

// A namespace's page: the catalog scoped to /datasets/{namespace}.
export default async function DatasetNamespacePage({
  params,
}: {
  params: Promise<{ namespace: string }>;
}) {
  const { namespace } = await params;
  return (
    <Suspense fallback={<PageSkeleton withFilters><CardGridSkeleton /></PageSkeleton>}>
      <DatasetsClient namespace={decodeURIComponent(namespace)} />
    </Suspense>
  );
}
