"use client";

import { CopyPlus, FolderOpen, Loader2, Play } from "lucide-react";
import { toast } from "sonner";

import type { DatasetDetail } from "@/lib/api-types";
import { useUseDataset } from "@/lib/hub-queries";
import { toastError } from "@/lib/toast";
import { useProgressRouter } from "@/lib/use-progress-router";
import { Button } from "@/components/ui/button";

/**
 * "Use this dataset" → a private copy in the caller's processes. Once the caller
 * holds a copy, the primary action opens it and "Add another copy" makes a fresh
 * one (the API's `use` is idempotent per user + revision otherwise).
 */
export function UseDatasetButton({ ds }: { ds: DatasetDetail }) {
  const router = useProgressRouter();
  const use = useUseDataset(ds.namespace, ds.name);
  const newest = [...ds.viewer.my_logs].sort((a, b) => b.created_at.localeCompare(a.created_at))[0];
  const unusable = ds.status !== "active" || ds.revision.purged;

  const run = async (newCopy: boolean) => {
    try {
      const res = await use.mutateAsync(newCopy);
      if (res.created) toast.success(`Added "${res.log.name}" to your processes`);
      router.push(`/processes/${res.log.id}`);
    } catch (err) {
      toastError(`Could not use this dataset: ${(err as Error).message}`);
    }
  };

  if (unusable) {
    return (
      <Button disabled className="gap-2">
        <Play className="h-4 w-4" />
        Unavailable
      </Button>
    );
  }

  if (!newest) {
    return (
      <Button onClick={() => run(false)} disabled={use.isPending} className="cursor-pointer gap-2">
        {use.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
        Use this dataset
      </Button>
    );
  }

  return (
    <div className="flex flex-wrap items-center gap-2">
      <Button
        onClick={() => router.push(`/processes/${newest.id}`)}
        className="cursor-pointer gap-2"
      >
        <FolderOpen className="h-4 w-4" />
        Open my copy
      </Button>
      <Button
        variant="outline"
        onClick={() => run(true)}
        disabled={use.isPending}
        className="cursor-pointer gap-2"
      >
        {use.isPending ? (
          <Loader2 className="h-4 w-4 animate-spin" />
        ) : (
          <CopyPlus className="h-4 w-4" />
        )}
        Add another copy
      </Button>
    </div>
  );
}
