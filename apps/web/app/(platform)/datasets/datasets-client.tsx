"use client";

import { useEffect, useMemo, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Database, Loader2, Search, SearchX } from "lucide-react";

import type { DatasetSort } from "@/lib/api-types";
import { useDatasets, useHubStatus, useLicenses } from "@/lib/hub-queries";
import { useDebouncedValue } from "@/lib/use-debounced-value";
import { PageContainer, PageHeader } from "@/components/page";
import { EmptyState } from "@/components/empty-state";
import { CardGridSkeleton } from "@/components/skeletons";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { DatasetCard } from "@/components/datasets/dataset-card";

const ALL = "__all__";

function readSort(v: string | null): DatasetSort {
  return v === "uses" ? "uses" : "updated";
}

export function DatasetsClient() {
  const router = useRouter();
  const pathname = usePathname();
  const sp = useSearchParams();
  const { data: status } = useHubStatus();
  const hubOff = status?.enabled === false;

  // The search box is local state (instant typing); the URL follows debounced,
  // and is the source of truth for the query.
  const [qInput, setQInput] = useState(sp.get("q") ?? "");
  const q = useDebouncedValue(qInput.trim(), 250);
  const license = sp.get("license") ?? undefined;
  const sort = readSort(sp.get("sort"));

  const setParam = (key: string, value: string | null) => {
    const next = new URLSearchParams(sp.toString());
    if (value) next.set(key, value);
    else next.delete(key);
    const qs = next.toString();
    router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
  };

  useEffect(() => {
    if ((sp.get("q") ?? "") !== q) setParam("q", q || null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q]);

  const params = useMemo(() => ({ q: q || undefined, license, sort }), [q, license, sort]);
  const { data: licenses } = useLicenses(!hubOff);
  const list = useDatasets(params, !hubOff);
  const items = useMemo(() => list.data?.pages.flatMap((p) => p.items) ?? [], [list.data]);
  const total = list.data?.pages[0]?.total ?? 0;
  const filtered = !!(q || license);

  if (hubOff) {
    return (
      <PageContainer>
        <EmptyState
          icon={Database}
          title="Datasets are not enabled"
          description="An administrator has turned the dataset hub off on this instance."
        />
      </PageContainer>
    );
  }

  return (
    <PageContainer>
      <PageHeader className="items-center">
        <div className="relative w-full max-w-md">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={qInput}
            onChange={(e) => setQInput(e.target.value)}
            placeholder="Search title, summary, name or tags"
            aria-label="Search datasets"
            className="h-9 pl-8"
          />
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Select
            value={license ?? ALL}
            onValueChange={(v) => setParam("license", v === ALL ? null : v)}
          >
            <SelectTrigger className="h-9 w-48 text-sm" aria-label="Filter by license">
              <SelectValue placeholder="Any license" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL}>Any license</SelectItem>
              {(licenses ?? []).map((l) => (
                <SelectItem key={l.id} value={l.id}>
                  {l.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Select
            value={sort}
            onValueChange={(v) => setParam("sort", v === "updated" ? null : v)}
          >
            <SelectTrigger className="h-9 w-44 text-sm" aria-label="Sort">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="updated">Recently updated</SelectItem>
              <SelectItem value="uses">Most used</SelectItem>
            </SelectContent>
          </Select>
        </div>
      </PageHeader>

      {list.isLoading ? (
        <CardGridSkeleton />
      ) : list.isError ? (
        <EmptyState
          icon={Database}
          title="Couldn't load datasets"
          description={(list.error as Error)?.message ?? "Unknown error"}
        />
      ) : items.length === 0 ? (
        filtered ? (
          <EmptyState
            icon={SearchX}
            title="No datasets match"
            description="Try other words or another license."
            primaryAction={
              <Button
                variant="outline"
                className="cursor-pointer"
                onClick={() => {
                  setQInput("");
                  router.replace(pathname, { scroll: false });
                }}
              >
                Clear filters
              </Button>
            }
          />
        ) : (
          <EmptyState
            icon={Database}
            title="No datasets yet"
            description="Published event logs appear here. Use one to get your own copy, ready to analyse."
          />
        )
      ) : (
        <>
          <p className="mb-3 text-xs text-muted-foreground">
            {total} dataset{total === 1 ? "" : "s"}
          </p>
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4">
            {items.map((ds) => (
              <DatasetCard key={ds.id} ds={ds} />
            ))}
          </div>
          {list.hasNextPage && (
            <div className="mt-6 flex justify-center">
              <Button
                variant="outline"
                onClick={() => list.fetchNextPage()}
                disabled={list.isFetchingNextPage}
                className="cursor-pointer gap-2"
              >
                {list.isFetchingNextPage && <Loader2 className="h-4 w-4 animate-spin" />}
                Load more
              </Button>
            </div>
          )}
        </>
      )}
    </PageContainer>
  );
}
