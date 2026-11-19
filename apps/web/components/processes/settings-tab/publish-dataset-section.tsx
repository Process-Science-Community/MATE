"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { Database, Loader2, Upload } from "lucide-react";
import { toast } from "sonner";

import type { EventLogDetail } from "@/lib/api-types";
import { usePublishCandidates, usePublishDataset } from "@/lib/hub-queries";
import { toastError } from "@/lib/toast";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DatasetCardForm,
  cardFieldsFromForm,
  emptyCardForm,
  validateCardForm,
  type CardFormState,
} from "@/components/datasets/dataset-card-form";

import { SectionShell } from "./general-section";

/**
 * Curators only (admin, not sandboxed): publish this log as a dataset. The API
 * snapshots the Parquet + fresh module results into an immutable revision; the
 * log itself is never written to.
 */
export function PublishDatasetSection({ logId, log }: { logId: string; log: EventLogDetail }) {
  const [open, setOpen] = useState(false);
  const hasFilter = (log.active_filter?.length ?? 0) > 0;
  const notReady = log.status !== "ready";
  const published = log.published_datasets ?? [];

  return (
    <SectionShell
      title="Publish as dataset"
      description="Share a frozen copy of this log in the dataset catalog. Users get their own copy, with your module results precomputed."
    >
      <div className="space-y-3">
        {published.length > 0 && (
          <ul className="space-y-1 text-sm">
            {published.map((d) => (
              <li key={d.slug} className="flex items-center gap-2">
                <Database className="h-3.5 w-3.5 text-muted-foreground" />
                <Link href={`/datasets/${d.slug}`} className="underline-offset-2 hover:underline">
                  {d.title}
                </Link>
                <span className="font-mono text-xs text-muted-foreground">{d.slug}</span>
              </li>
            ))}
          </ul>
        )}
        <div className="flex flex-wrap items-center gap-3">
          <Button
            variant="outline"
            onClick={() => setOpen(true)}
            disabled={hasFilter || notReady}
            className="cursor-pointer gap-2"
          >
            <Upload className="h-3.5 w-3.5" />
            Publish as dataset…
          </Button>
          {hasFilter && (
            <span className="text-xs text-muted-foreground">
              Clear the committed filter (Events tab) before publishing.
            </span>
          )}
          {!hasFilter && notReady && (
            <span className="text-xs text-muted-foreground">The log must finish importing first.</span>
          )}
        </div>
      </div>
      {open && <PublishDialog logId={logId} log={log} onClose={() => setOpen(false)} />}
    </SectionShell>
  );
}

function PublishDialog({
  logId,
  log,
  onClose,
}: {
  logId: string;
  log: EventLogDetail;
  onClose: () => void;
}) {
  const publish = usePublishDataset();
  // The server's list of shippable results (fresh precompute results only), not
  // the run list: the dialog then offers exactly what a default publish ships.
  const { data: candidates, isLoading, isError } = usePublishCandidates(logId);
  const [form, setForm] = useState<CardFormState>(() =>
    emptyCardForm({ title: log.name.slice(0, 200), summary: (log.description ?? "").slice(0, 300) }),
  );
  const [touched, setTouched] = useState(false);

  const all = useMemo(() => (candidates ?? []).map((m) => m.module_id), [candidates]);
  // null = untouched: ship every candidate.
  const [picked, setPicked] = useState<Set<string> | null>(null);
  const selected = picked ?? new Set(all);

  const errors = validateCardForm(form, "create");
  const valid = Object.keys(errors).length === 0;

  const toggle = (id: string, on: boolean) => {
    const next = new Set(selected);
    if (on) next.add(id);
    else next.delete(id);
    setPicked(next);
  };

  const onSubmit = async () => {
    setTouched(true);
    if (!valid) return;
    try {
      await publish.mutateAsync({
        ...cardFieldsFromForm(form),
        source_log_id: logId,
        namespace: form.namespace,
        name: form.name,
        modules: picked === null ? null : [...picked],
      });
      toast.success(`Published ${form.namespace}/${form.name}`);
      onClose();
    } catch (err) {
      toastError(`Publish failed: ${(err as Error).message}`);
    }
  };

  const customised = (candidates ?? []).filter((m) => !m.config_is_default);

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>Publish &ldquo;{log.name}&rdquo; as a dataset</DialogTitle>
          <DialogDescription>
            Start private, check the page, then widen visibility. Only the Parquet data and the
            selected module results are copied – never the original upload.
          </DialogDescription>
        </DialogHeader>

        <DatasetCardForm
          mode="create"
          value={form}
          onChange={setForm}
          errors={touched ? errors : {}}
        />

        <div className="space-y-2 rounded-md border border-border/60 p-3">
          <p className="text-sm font-medium">Precomputed module results</p>
          <p className="text-[11px] text-muted-foreground">
            Results of modules that precompute on import, up to date for this log, ship with the
            dataset and open instantly for users on the default module settings. Run a module on
            this log first to make it appear here. Other modules compute when a user opens them.
          </p>
          {isLoading ? (
            <p className="text-xs text-muted-foreground">Loading…</p>
          ) : isError ? (
            <p className="text-xs text-destructive">Couldn&apos;t load the shippable results.</p>
          ) : all.length === 0 ? (
            <p className="text-xs text-muted-foreground">
              No up-to-date precomputed results on this log – users compute every module
              themselves.
            </p>
          ) : (
            <ul className="grid gap-1.5 sm:grid-cols-2">
              {(candidates ?? []).map((m) => (
                <li key={m.module_id}>
                  <label className="flex cursor-pointer items-center gap-2 text-sm">
                    <Checkbox
                      checked={selected.has(m.module_id)}
                      onCheckedChange={(v) => toggle(m.module_id, v === true)}
                    />
                    <span>{m.name}</span>
                    <span className="font-mono text-[11px] text-muted-foreground">{m.module_id}</span>
                  </label>
                </li>
              ))}
            </ul>
          )}
          {customised.length > 0 && (
            <p className="text-[11px] text-amber-700 dark:text-amber-400">
              Your settings differ from the defaults for{" "}
              {customised.map((m) => m.name).join(", ")}. Users on default settings will recompute
              these instead of using your results – reset the module settings to their defaults
              and rerun before publishing.
            </p>
          )}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={onClose} className="cursor-pointer">
            Cancel
          </Button>
          <Button
            onClick={onSubmit}
            disabled={publish.isPending || isLoading || (touched && !valid)}
            className="cursor-pointer"
          >
            {publish.isPending && <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />}
            Publish
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
