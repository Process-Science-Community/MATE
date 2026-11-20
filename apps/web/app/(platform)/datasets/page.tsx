import { Suspense } from "react";

import { CardGridSkeleton, PageSkeleton } from "@/components/skeletons";
import { DatasetsClient } from "./datasets-client";

// Server shell only – the catalog loads client-side (URL-synced search state
// needs `useSearchParams`, hence the Suspense boundary).
export default function DatasetsPage() {
  return (
    <Suspense fallback={<PageSkeleton withFilters><CardGridSkeleton /></PageSkeleton>}>
      <DatasetsClient />
    </Suspense>
  );
}
