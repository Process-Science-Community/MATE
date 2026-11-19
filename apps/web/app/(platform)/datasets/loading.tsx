import { CardGridSkeleton, PageSkeleton } from "@/components/skeletons";

export default function Loading() {
  return <PageSkeleton withFilters><CardGridSkeleton /></PageSkeleton>;
}
