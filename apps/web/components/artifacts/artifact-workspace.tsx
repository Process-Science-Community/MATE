"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { Boxes, Download, Loader2, Pin, PinOff, Play, RotateCw, Trash2, Upload } from "lucide-react";
import { toast } from "sonner";

import { ARTIFACT_TYPES } from "../../../../packages/module-sdk-ts/src/artifacts.generated";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { EmptyState } from "@/components/empty-state";
import { formatBytes } from "@/components/processes/import/file-step";
import { cn } from "@/lib/cn";
import { downloadBlob } from "@/lib/download";
import { formatRelative } from "@/lib/format";
import { useRunModule } from "@/lib/module-run-queries";
import { useEventLogs } from "@/lib/queries";
import { artifactTypeId } from "@/lib/visualizations/adapters";
import {
  useArtifact,
  useArtifacts,
  useDeleteArtifact,
  useModuleVariant,
  useRerunArtifact,
  useUpdateArtifact,
  type ArtifactRow,
} from "@/lib/artifact-queries";

import { ImportArtifactDialog, UseInDialog } from "./artifact-dialogs";
import { ArtifactLineage } from "./artifact-lineage";
import { ArtifactViewer } from "./artifact-viewer";

const ALL_LOGS = "__all__";
const ARTIFACT_STORAGE: Record<string, { storage: string }> = ARTIFACT_TYPES;

/** The artifact workspace: every model/table the user's modules produced or the user
 *  imported, grouped by artifact type, with a viewer and actions. `logId` scopes it
 *  to one log (the process page's Artifacts tab); without it a log filter shows. */
export function ArtifactWorkspace({ logId }: { logId?: string }) {
  const [logFilter, setLogFilter] = useState(ALL_LOGS);
  const [pinnedOnly, setPinnedOnly] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [importOpen, setImportOpen] = useState(false);
  const scopedLog = logId ?? (logFilter === ALL_LOGS ? undefined : logFilter);
  const filtered = pinnedOnly || scopedLog !== logId;

  const list = useArtifacts({ log_id: scopedLog, pinned: pinnedOnly || undefined });
  const { data: logs } = useEventLogs();

  const rows = useMemo(() => list.data ?? [], [list.data]);
  // A filter change can drop the open artifact from the list: fall back to the first.
  // Checked once the new filter's list is in (not the previous list as placeholder).
  const filterKey = `${scopedLog}:${pinnedOnly}`;
  const [checkedKey, setCheckedKey] = useState(filterKey);
  if (checkedKey !== filterKey && list.data && !list.isPlaceholderData) {
    setCheckedKey(filterKey);
    if (selected && !list.data.some((a) => a.id === selected)) setSelected(list.data[0]?.id ?? null);
  }
  // `?ref=` (the flow editor's "Open in workspace"): select that artifact; one outside
  // the list's scope is looked up under its own log (a ref starts with its log id).
  const refParam = useSearchParams().get("ref");
  const [seenRef, setSeenRef] = useState<string | null>(null);
  const [pendingRef, setPendingRef] = useState<string | null>(null);
  const [revealId, setRevealId] = useState<string | null>(null);
  if (refParam !== seenRef) {
    setSeenRef(refParam);
    setPendingRef(refParam);
  }
  if (pendingRef && list.data && !list.isPlaceholderData) {
    const hit = list.data.find((a) => a.ref === pendingRef);
    const refLog = pendingRef.split("/")[0] ?? "";
    if (hit) {
      setPendingRef(null);
      setSelected(hit.id);
      setRevealId(hit.id);
    } else if (!logId && refLog !== "_" && (logFilter !== refLog || pinnedOnly)) {
      setPinnedOnly(false);
      setLogFilter(refLog);
    } else {
      setPendingRef(null);
    }
  }
  useEffect(() => {
    if (!revealId) return;
    document.getElementById(`artifact-${revealId}`)?.scrollIntoView({ block: "center" });
    setRevealId(null);
  }, [revealId]);
  const groups = useMemo(() => {
    const byType = new Map<string, ArtifactRow[]>();
    for (const a of rows) {
      const type = artifactTypeId(a.artifact_type) ?? a.artifact_type;
      byType.set(type, [...(byType.get(type) ?? []), a]);
    }
    return [...byType];
  }, [rows]);
  const selectedId = selected ?? rows[0]?.id ?? null;
  const logName = (id: string | null) => logs?.find((l) => l.id === id)?.name;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        {!logId && (
          <Select value={logFilter} onValueChange={setLogFilter}>
            <SelectTrigger size="sm" className="w-56" aria-label="Event log">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL_LOGS}>All event logs</SelectItem>
              {logs?.map((l) => (
                <SelectItem key={l.id} value={l.id}>
                  {l.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}
        <Button
          variant={pinnedOnly ? "secondary" : "outline"}
          size="sm"
          aria-pressed={pinnedOnly}
          onClick={() => setPinnedOnly((p) => !p)}
        >
          <Pin /> Pinned only
        </Button>
        <Button size="sm" className="ml-auto" onClick={() => setImportOpen(true)}>
          <Upload /> Import
        </Button>
      </div>

      {list.isLoading ? (
        <div className="grid gap-4 lg:grid-cols-[320px_minmax(0,1fr)]">
          <Skeleton className="h-80 w-full" />
          <Skeleton className="h-[520px] w-full" />
        </div>
      ) : rows.length === 0 ? (
        filtered ? (
          <EmptyState
            icon={Boxes}
            title="Nothing matches"
            description="No artifact matches these filters."
            primaryAction={
              <Button
                variant="outline"
                onClick={() => {
                  setPinnedOnly(false);
                  setLogFilter(ALL_LOGS);
                }}
              >
                Clear filters
              </Button>
            }
          />
        ) : (
          <EmptyState
            icon={Boxes}
            title="No artifacts yet"
            description={
              logId
                ? "Models and tables a module computes for this log land here. Open a module such as Discovery on the Overview tab, or import a JSON model."
                : "Models and tables your modules compute land here. Open a process and run a module such as Discovery, or import a JSON model."
            }
            primaryAction={
              <Button onClick={() => setImportOpen(true)}>
                <Upload /> Import
              </Button>
            }
            secondaryAction={
              !logId && (
                <Button asChild variant="outline">
                  <Link href="/processes">Open a process</Link>
                </Button>
              )
            }
          />
        )
      ) : (
        <div className="grid items-start gap-4 lg:grid-cols-[320px_minmax(0,1fr)]">
          <nav aria-label="Artifacts" className="space-y-3 rounded-lg border bg-card p-1.5">
            {groups.map(([type, items]) => (
              <section key={type}>
                <h3 className="flex items-center gap-1.5 px-2 pb-1 pt-1.5 text-xs font-medium text-muted-foreground">
                  {type}
                  <span className="tabular-nums">{items.length}</span>
                </h3>
                <ul>
                  {items.map((a) => (
                    <li key={a.id} id={`artifact-${a.id}`}>
                      <ArtifactListItem
                        artifact={a}
                        producer={producerOf(a)}
                        active={a.id === selectedId}
                        onSelect={() => setSelected(a.id)}
                      />
                    </li>
                  ))}
                </ul>
              </section>
            ))}
          </nav>
          {selectedId && (
            <ArtifactDetailPanel id={selectedId} logName={logName} onSelect={setSelected} />
          )}
        </div>
      )}

      <ImportArtifactDialog
        open={importOpen}
        onOpenChange={setImportOpen}
        defaultLogId={logId}
        onImported={setSelected}
      />
    </div>
  );
}

const producerOf = (a: ArtifactRow) =>
  a.source === "upload" ? "Imported" : (a.module_name ?? a.module_id ?? "");

/** Computed by another version of its module than the one loaded now (an upgrade or
 *  rollback since). */
const isOlderVersion = (a: ArtifactRow) =>
  a.source === "run" &&
  !!a.producer_version &&
  !!a.current_version &&
  a.producer_version !== a.current_version;

const olderVersionTitle = (a: ArtifactRow) =>
  `Computed by ${a.producer_version}; the module is now ${a.current_version}.`;

function ArtifactListItem({
  artifact: a,
  producer,
  active,
  onSelect,
}: {
  artifact: ArtifactRow;
  producer: string;
  active: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onSelect}
      aria-current={active || undefined}
      className={cn(
        "w-full cursor-pointer rounded-md px-2 py-1.5 text-left outline-none transition-colors hover:bg-muted/60 focus-visible:ring-2 focus-visible:ring-ring/50",
        active && "bg-muted",
      )}
    >
      <div className="flex items-center gap-1.5">
        <span className="min-w-0 flex-1 truncate text-sm font-medium">{a.name}</span>
        {a.pinned && <Pin className="h-3 w-3 shrink-0 text-primary" aria-label="Pinned" />}
      </div>
      <div className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
        <span className="truncate">{producer}</span>
        {a.variant && (
          <Badge variant="outline" className="h-4 shrink-0 px-1 text-[10px] font-normal">
            variant {a.variant.slice(0, 6)}
          </Badge>
        )}
        {a.state !== "ready" && (
          <Badge
            variant={a.state === "failed" ? "destructive" : "secondary"}
            className="h-4 shrink-0 px-1 text-[10px] font-normal"
          >
            {a.state}
          </Badge>
        )}
        {isOlderVersion(a) && (
          <Badge
            variant="outline"
            className="h-4 shrink-0 px-1 text-[10px] font-normal"
            title={olderVersionTitle(a)}
          >
            older version
          </Badge>
        )}
        <span className="ml-auto shrink-0 tabular-nums">
          {a.size_bytes != null && `${formatBytes(a.size_bytes)} · `}
          {formatRelative(a.updated_at)}
        </span>
      </div>
    </button>
  );
}

function NameField({ artifact }: { artifact: ArtifactRow }) {
  const update = useUpdateArtifact();
  const [draft, setDraft] = useState(artifact.name);
  const commit = () => {
    const name = draft.trim();
    if (!name || name === artifact.name) return setDraft(artifact.name);
    update.mutate({ id: artifact.id, name }, { onError: (e) => toast.error(e.message) });
  };
  return (
    <Input
      value={draft}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => {
        if (e.key === "Enter") e.currentTarget.blur();
        if (e.key === "Escape") setDraft(artifact.name);
      }}
      aria-label="Artifact name"
      className="field-sizing-content h-8 min-w-24 max-w-md border-transparent px-1.5 text-base font-semibold shadow-none hover:border-input focus-visible:border-input dark:bg-transparent"
    />
  );
}

function ArtifactDetailPanel({
  id,
  logName,
  onSelect,
}: {
  id: string;
  logName: (logId: string | null) => string | undefined;
  onSelect: (id: string | null) => void;
}) {
  const { data: a, isLoading, isError } = useArtifact(id);
  const update = useUpdateArtifact();
  const remove = useDeleteArtifact();
  const [useInOpen, setUseInOpen] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);

  if (isLoading) return <Skeleton className="h-[600px] w-full" />;
  if (isError || !a) {
    return (
      <p className="rounded-lg border bg-card p-6 text-sm text-muted-foreground">
        This artifact is gone – its module result was removed or recomputed without it.
      </p>
    );
  }

  const defaultRun = a.source === "run" && !a.variant;
  // A pinned variant whose rerun failed or is under way: no content to show or use.
  const rerunning = a.state === "failed" || a.state === "running";
  const parquet = ARTIFACT_STORAGE[artifactTypeId(a.artifact_type) ?? ""]?.storage === "parquet";
  // The stored file itself, not the (row-capped) preview the viewer shows.
  const download = () =>
    downloadBlob(`/api/v1/artifacts/${a.id}/download`, `${a.name}.${parquet ? "parquet" : "json"}`);
  const deleteButton = (
    <Button
      variant="outline"
      size="sm"
      disabled={defaultRun}
      onClick={() => setConfirmDelete(true)}
    >
      <Trash2 /> Delete
    </Button>
  );

  return (
    <section className="min-w-0 space-y-4" aria-label={a.name}>
      <div className="flex flex-wrap items-center gap-2">
        <NameField key={`${a.id}:${a.name}`} artifact={a} />
        <Badge variant="secondary">{a.artifact_type}</Badge>
        {a.variant && <Badge variant="outline">variant {a.variant.slice(0, 6)}</Badge>}
        {isOlderVersion(a) && !rerunning && <OlderVersion artifact={a} />}
        <div className="ml-auto flex flex-wrap items-center gap-1.5">
          <Button size="sm" disabled={rerunning} onClick={() => setUseInOpen(true)}>
            <Play /> Use in…
          </Button>
          <Button
            variant="outline"
            size="sm"
            aria-pressed={a.pinned}
            title={a.variant ? "Pinned variant results are kept when old variants are cleaned up." : undefined}
            onClick={() =>
              update.mutate({ id: a.id, pinned: !a.pinned }, { onError: (e) => toast.error(e.message) })
            }
          >
            {a.pinned ? <PinOff /> : <Pin />} {a.pinned ? "Unpin" : "Pin"}
          </Button>
          <Button variant="outline" size="sm" disabled={rerunning} onClick={download}>
            <Download /> {parquet ? "Parquet" : "JSON"}
          </Button>
          {defaultRun ? (
            <Tooltip>
              <TooltipTrigger asChild>
                <span tabIndex={0} className="inline-flex cursor-not-allowed">
                  {deleteButton}
                </span>
              </TooltipTrigger>
              <TooltipContent side="bottom">
                Default results are managed by the module – they update when it recomputes.
              </TooltipContent>
            </Tooltip>
          ) : (
            deleteButton
          )}
        </div>
      </div>

      <p className="text-xs text-muted-foreground">
        {a.source === "upload" ? "Imported" : `By ${producerOf(a)}`}
        {a.producer_version && ` ${a.producer_version}`}
        {a.log_id && (
          <>
            {" · "}
            <Link href={`/processes/${a.log_id}`} className="underline-offset-4 hover:underline">
              {logName(a.log_id) ?? "event log"}
            </Link>
          </>
        )}
        {a.size_bytes != null && ` · ${formatBytes(a.size_bytes)}`}
        {` · updated ${formatRelative(a.updated_at)}`}
      </p>

      {rerunning ? <VariantRunNotice artifact={a} /> : <ArtifactViewer artifact={a} />}

      <div className="space-y-2">
        <h3 className="text-sm font-medium">Lineage</h3>
        <ArtifactLineage artifact={a} onSelect={onSelect} />
      </div>

      <UseInDialog key={a.id} artifact={a} open={useInOpen} onOpenChange={setUseInOpen} />
      <AlertDialog open={confirmDelete} onOpenChange={setConfirmDelete}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete “{a.name}”?</AlertDialogTitle>
            <AlertDialogDescription>
              {a.variant
                ? "This deletes the whole run variant, including its other results. Running the module with the same inputs again recreates it."
                : "The imported file is removed from the workspace. This can't be undone."}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
              onClick={() => {
                onSelect(null);
                remove.mutate(a.id, { onError: (e) => toast.error(e.message) });
              }}
            >
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </section>
  );
}

/** "older version" with "Re-run": a variant reruns its spec, a default result recomputes
 *  the log's run; the workspace refreshes when it settles. */
function OlderVersion({ artifact: a }: { artifact: ArtifactRow }) {
  const rerunVariant = useRerunArtifact();
  const runDefault = useRunModule(a.log_id ?? "", a.module_id ?? "");
  const onError = (e: Error) => toast.error(e.message);
  const onSuccess = () => toast.success(`Re-running with ${a.current_version}`);
  return (
    <>
      <Badge variant="outline" title={olderVersionTitle(a)}>
        older version
      </Badge>
      <Button
        variant="ghost"
        size="sm"
        disabled={rerunVariant.isPending || runDefault.isPending}
        title={olderVersionTitle(a)}
        onClick={() =>
          a.variant
            ? rerunVariant.mutate(a, { onError, onSuccess })
            : runDefault.mutate(true, { onError, onSuccess })
        }
      >
        <RotateCw /> Re-run
      </Button>
    </>
  );
}

/** A pinned variant whose rerun failed (with "Re-run") or is under way (polled until it
 *  settles, which refreshes the workspace). */
function VariantRunNotice({ artifact: a }: { artifact: ArtifactRow }) {
  const rerun = useRerunArtifact();
  const { data: run } = useModuleVariant(a.log_id ?? "", a.module_id ?? "", a.variant);
  return (
    <div className="flex h-[520px] w-full flex-col items-center justify-center gap-3 rounded-lg border bg-card p-6 text-center text-sm text-muted-foreground">
      {a.state === "running" ? (
        <p className="flex items-center gap-2">
          <Loader2 className="h-4 w-4 animate-spin" /> This variant is being recomputed…
        </p>
      ) : (
        <>
          <p>The last run of this variant failed, so there is no result to show.</p>
          {run?.error && <p className="line-clamp-4 max-w-prose break-words text-xs">{run.error}</p>}
          <Button
            size="sm"
            disabled={rerun.isPending}
            onClick={() => rerun.mutate(a, { onError: (e) => toast.error(e.message) })}
          >
            <RotateCw /> Re-run
          </Button>
        </>
      )}
    </div>
  );
}
