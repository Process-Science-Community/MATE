"use client";

import Link from "next/link";
import { useState } from "react";
import { Ban, Database, Trash2 } from "lucide-react";
import { toast } from "sonner";

import type { EventLogSummary, SourceDataset } from "@/lib/api-types";
import { useDeleteEventLog } from "@/lib/queries";
import { toastError } from "@/lib/toast";
import { useProgressRouter } from "@/lib/use-progress-router";
import { Button } from "@/components/ui/button";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { LicenseBadge } from "./license-badge";

export function datasetHref(src: Pick<SourceDataset, "slug">): string {
  return `/datasets/${src.slug}`;
}

/** A derived log whose dataset was disabled or purged: its data routes answer 410. */
export function isWithdrawn(src: SourceDataset | null | undefined): boolean {
  return !!src && src.status !== "active";
}

function truncate(s: string, n: number): string {
  return s.length > n ? `${s.slice(0, n - 1).trimEnd()}…` : s;
}

/**
 * License / citation strip on every log copied from a dataset. When the dataset
 * was withdrawn it becomes the only thing the page shows – a destructive panel
 * with a way to delete the (now unreadable) copy.
 */
export function DatasetLineageBanner({ log }: { log: EventLogSummary }) {
  const src = log.source_dataset;
  if (!src) return null;
  if (isWithdrawn(src)) return <WithdrawnPanel log={log} src={src} />;

  return (
    <div className="mb-4 flex flex-wrap items-center gap-x-2 gap-y-1.5 rounded-lg border border-primary/20 bg-primary/5 px-3 py-2 text-xs">
      <Database className="h-3.5 w-3.5 shrink-0 text-primary" aria-hidden />
      <span>
        From{" "}
        <Link href={datasetHref(src)} className="font-semibold underline-offset-2 hover:underline">
          {src.title}
        </Link>{" "}
        <span className="font-mono text-muted-foreground">
          ({src.slug} r{src.revision})
        </span>
      </span>
      <span className="text-muted-foreground">·</span>
      <LicenseBadge
        license={{ id: src.license_id, name: src.license_name, url: src.license_url }}
      />
      {src.citation && (
        <>
          <span className="text-muted-foreground">·</span>
          <span className="min-w-0 flex-1 truncate text-muted-foreground" title={src.citation}>
            Cite:{" "}
            <Link
              href={`${datasetHref(src)}#citation`}
              className="underline-offset-2 hover:underline"
            >
              {truncate(src.citation, 140)}
            </Link>
          </span>
        </>
      )}
    </div>
  );
}

function WithdrawnPanel({ log, src }: { log: EventLogSummary; src: SourceDataset }) {
  const del = useDeleteEventLog();
  const router = useProgressRouter();
  const [open, setOpen] = useState(false);

  const onDelete = async () => {
    try {
      await del.mutateAsync(log.id);
      toast.success(`Deleted "${log.name}"`);
      router.push("/processes");
    } catch (err) {
      toastError(`Delete failed: ${(err as Error).message}`);
    }
  };

  return (
    <div
      role="alert"
      className="mx-auto mt-6 flex max-w-2xl flex-col gap-3 rounded-lg border border-destructive/40 bg-destructive/5 p-5"
    >
      <div className="flex items-start gap-3">
        <Ban className="mt-0.5 h-5 w-5 shrink-0 text-destructive" aria-hidden />
        <div className="space-y-1">
          <p className="text-sm font-semibold text-destructive">
            This dataset was withdrawn{src.disabled_reason ? `: ${src.disabled_reason}` : "."}
          </p>
          <p className="text-sm text-muted-foreground">
            &ldquo;{log.name}&rdquo; is a copy of <span className="font-mono">{src.slug}</span>.
            Its data can no longer be opened.
          </p>
        </div>
      </div>
      <div className="flex justify-end">
        <Button
          variant="outline"
          className="cursor-pointer border-destructive/40 text-destructive hover:bg-destructive/10 hover:text-destructive"
          onClick={() => setOpen(true)}
          disabled={del.isPending}
        >
          <Trash2 className="mr-2 h-3.5 w-3.5" />
          Delete my copy
        </Button>
      </div>
      <AlertDialog open={open} onOpenChange={setOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete &ldquo;{log.name}&rdquo;?</AlertDialogTitle>
            <AlertDialogDescription>
              Removes your copy and its cached module results. This cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel className="cursor-pointer">Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={onDelete}
              className="cursor-pointer bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
