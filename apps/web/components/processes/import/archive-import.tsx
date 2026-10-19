"use client";

import { useCallback, useEffect, useId, useRef, useState } from "react";
import {
  AlertTriangle,
  Check,
  ChevronDown,
  Circle,
  CircleDot,
  Loader2,
  RotateCcw,
  X,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useAiConfig } from "@/lib/ai-queries";
import { useImportColumnMapping } from "@/lib/ai-guidance";
import { EV } from "@/lib/analytics/events";
import { useTrack } from "@/lib/analytics/hooks";
import { ApiError } from "@/lib/api";
import type { ArchiveMember, ArchiveProbeResponse, LogProbeResponse } from "@/lib/api-types";
import { cn } from "@/lib/cn";
import { formatNumber } from "@/lib/format";
import { useDiscardStaged, useExtractArchiveMember, useImportEventLog } from "@/lib/queries";
import { toastError } from "@/lib/toast";
import { aiMappingRequest, mergeAiSuggestions } from "@/components/processes/import/ai-mapping";
import { ArchivePicker, formatLabel } from "@/components/processes/import/archive-picker";
import {
  BatchImportProgress,
  type QueuedImport,
} from "@/components/processes/import/batch-import-progress";
import { displayNameFor } from "@/components/processes/import/file-step";
import {
  hasLowConfidence,
  initialMapping,
  MappingStep,
  missingRequiredRoles,
  parserMappingFor,
  type MappingState,
} from "@/components/processes/import/mapping-step";
import { ROLE_LABELS } from "@/components/processes/import/role-card";

export type ArchiveStep = "pick" | "review" | "importing" | "running";

/** One picked member, from "waiting to be unpacked" to a confirmed mapping. */
interface PickedLog {
  member: ArchiveMember;
  status: "waiting" | "unpacking" | "ready" | "failed";
  error: string | null;
  probe: LogProbeResponse | null;
  /** The probe's own mapping – AI suggestions only fill roles it was unsure of. */
  base: MappingState | null;
  mapping: MappingState | null;
  confirmed: boolean;
  name: string;
  aiPending: boolean;
  aiSuggested: Set<string>;
}

type ReadyLog = PickedLog & { probe: LogProbeResponse; base: MappingState; mapping: MappingState };

function isReady(log: PickedLog): log is ReadyLog {
  return log.status === "ready" && log.probe !== null && log.mapping !== null;
}

function pickedLog(member: ArchiveMember): PickedLog {
  return {
    member,
    status: "waiting",
    error: null,
    probe: null,
    base: null,
    mapping: null,
    confirmed: false,
    name: displayNameFor(member.name),
    aiPending: false,
    aiSuggested: new Set(),
  };
}

/** The server's own words, without the `API 413:` prefix a toast would carry. */
function errorText(err: unknown): string {
  if (err instanceof ApiError) {
    const body = err.detail;
    const inner =
      body && typeof body === "object" && "detail" in body
        ? (body as { detail: unknown }).detail
        : body;
    if (typeof inner === "string" && inner) return inner;
  }
  return err instanceof Error && err.message ? err.message : "Something went wrong.";
}

/**
 * An archive holding several event logs, from "which ones?" to their imports:
 *
 *   pick → each picked member unpacked + probed on its own → confirm each
 *   mapping → import them one after another → watch them land
 *
 * Server-side every member becomes an ordinary staged upload, so each log gets
 * exactly the mapping step a single file gets. Unpacking runs in the
 * background while the user reviews the logs that are already read.
 */
export function ArchiveImport({
  archive,
  onStepChange,
  onExit,
  onSuccess,
}: {
  archive: ArchiveProbeResponse;
  onStepChange?: (step: ArchiveStep) => void;
  /** Leave the archive flow. Its staged bytes are already dropped by then. */
  onExit: () => void;
  /** Set by onboarding: hand off the first queued log instead of showing progress. */
  onSuccess?: (logId: string) => void;
}) {
  const track = useTrack();
  const extract = useExtractArchiveMember();
  const importer = useImportEventLog();
  const { mutate: discardStaged } = useDiscardStaged();
  const aiMapping = useImportColumnMapping();
  const { data: aiConfig } = useAiConfig();
  const aiConfigured = Boolean(aiConfig?.selected_provider && aiConfig?.selected_model);

  const [step, setStep] = useState<ArchiveStep>("pick");
  const [selected, setSelected] = useState<Set<string>>(
    () => new Set(archive.members.filter((m) => m.importable).map((m) => m.path)),
  );
  const [logs, setLogs] = useState<PickedLog[]>([]);
  const [openPath, setOpenPath] = useState<string | null>(null);
  const [queued, setQueued] = useState<QueuedImport[]>([]);
  const [importing, setImporting] = useState(0);

  // The unpack loop outlives renders: going Back (or leaving) bumps the run, and
  // a loop that finds itself outdated stops touching state.
  const runRef = useRef(0);

  useEffect(() => {
    onStepChange?.(step);
  }, [onStepChange, step]);

  const patch = useCallback((path: string, changes: Partial<PickedLog>) => {
    setLogs((prev) => prev.map((l) => (l.member.path === path ? { ...l, ...changes } : l)));
  }, []);

  // Same contract as the single-file flow: a best-effort second opinion on the
  // roles the resolver guessed, silent when it fails.
  const suggestWithAi = useCallback(
    async (path: string, probe: LogProbeResponse, base: MappingState) => {
      const request = aiMappingRequest(probe);
      patch(path, { aiPending: true });
      try {
        const res = await aiMapping.mutateAsync(request);
        setLogs((prev) =>
          prev.map((l) => {
            if (l.member.path !== path) return l;
            // Confirmed meanwhile: the user signed off on what they saw.
            if (!l.mapping || l.confirmed) return { ...l, aiPending: false };
            const merged = mergeAiSuggestions(l.mapping, base, request.headers, res.suggestions);
            return { ...l, mapping: merged.mapping, aiSuggested: merged.filled, aiPending: false };
          }),
        );
      } catch {
        patch(path, { aiPending: false });
      }
    },
    [aiMapping, patch],
  );

  // One member at a time: they share the archive's unpack budget server-side,
  // and the first log is ready to review while the rest are still unpacking.
  const unpack = useCallback(
    async (paths: string[], run: number) => {
      for (const path of paths) {
        if (runRef.current !== run) return;
        patch(path, { status: "unpacking", error: null });
        try {
          const probe = await extract.mutateAsync({
            stagingToken: archive.staging_token,
            member: path,
          });
          if (runRef.current !== run) return;
          const base = initialMapping(probe);
          patch(path, {
            status: "ready",
            probe,
            base,
            mapping: base,
            confirmed: false,
            aiSuggested: new Set(),
          });
          setOpenPath((open) => open ?? path);
          if (aiConfigured && probe.needs_mapping && hasLowConfidence(base)) {
            void suggestWithAi(path, probe, base);
          }
        } catch (err) {
          if (runRef.current !== run) return;
          patch(path, { status: "failed", error: errorText(err) });
        }
      }
    },
    [aiConfigured, archive.staging_token, extract, patch, suggestWithAi],
  );

  const continueWithSelection = useCallback(() => {
    const kept = new Map(logs.map((l) => [l.member.path, l]));
    // Unpacked earlier but deselected since: their staged copies go now.
    for (const log of logs) {
      if (!selected.has(log.member.path) && log.probe) discardStaged(log.probe.staging_token);
    }
    const next = archive.members
      .filter((m) => m.importable && selected.has(m.path))
      .map((m) => {
        const prior = kept.get(m.path);
        return prior && isReady(prior) ? prior : pickedLog(m);
      });
    setLogs(next);
    setOpenPath(next.find((l) => isReady(l) && !l.confirmed)?.member.path ?? null);
    setStep("review");
    track(EV.PROCESS_IMPORT_ARCHIVE_PICKED, {
      format: archive.archive_format,
      logs: archive.members.filter((m) => m.importable).length,
      picked: next.length,
    });
    runRef.current += 1;
    void unpack(
      next.filter((l) => !isReady(l)).map((l) => l.member.path),
      runRef.current,
    );
  }, [archive, discardStaged, logs, selected, track, unpack]);

  const back = useCallback(() => {
    runRef.current += 1;
    setStep("pick");
  }, []);

  const cancel = useCallback(() => {
    runRef.current += 1;
    discardStaged(archive.staging_token);
    for (const log of logs) if (log.probe) discardStaged(log.probe.staging_token);
    onExit();
  }, [archive.staging_token, discardStaged, logs, onExit]);

  const confirm = useCallback(
    (path: string) => {
      const index = logs.findIndex((l) => l.member.path === path);
      const log = logs[index];
      if (!log?.mapping) return;
      patch(path, { confirmed: true });
      track(EV.PROCESS_IMPORT_MAPPING_CONFIRMED, {
        changed_roles: Object.values(log.mapping.quality).filter((q) => q === "user").length,
        had_low_confidence: hasLowConfidence(log.mapping),
        source: "archive",
      });
      // Walk on to the next log still waiting for its sign-off.
      const rest = [...logs.slice(index + 1), ...logs.slice(0, index)];
      setOpenPath(rest.find((l) => isReady(l) && !l.confirmed)?.member.path ?? null);
    },
    [logs, patch, track],
  );

  const ready = logs.filter(isReady);
  const pending = logs.filter((l) => l.status === "waiting" || l.status === "unpacking").length;
  const failed = logs.filter((l) => l.status === "failed").length;
  const confirmed = ready.filter((l) => l.confirmed).length;
  const canImport =
    step === "review" && pending === 0 && ready.length > 0 && confirmed === ready.length;

  const importAll = useCallback(async () => {
    if (!canImport) return;
    setStep("importing");
    setOpenPath(null); // what's imported is what was confirmed - no edits mid-way
    const done: QueuedImport[] = [];
    for (const [index, log] of ready.entries()) {
      setImporting(index);
      const name = log.name.trim() || displayNameFor(log.member.name);
      try {
        const resp = await importer.mutateAsync({
          stagingToken: log.probe.staging_token,
          name,
          columnRoles: log.probe.needs_mapping ? log.mapping.roles : undefined,
          ...parserMappingFor(log.probe, log.mapping),
        });
        done.push({ logId: resp.log_id, jobId: resp.job_id, name });
        track(EV.PROCESS_IMPORT_FINISHED, {
          source: "archive",
          format: log.probe.source_format,
          ok: true,
        });
      } catch (err) {
        track(EV.PROCESS_IMPORT_FINISHED, {
          source: "archive",
          format: log.probe.source_format,
          ok: false,
        });
        patch(log.member.path, { status: "failed", error: errorText(err) });
      }
    }
    // Every picked member was staged on its own: the archive itself is spent.
    discardStaged(archive.staging_token);

    if (done.length === 0) {
      setStep("review");
      toastError("None of the logs could be imported.");
      return;
    }
    if (done.length < ready.length) {
      toastError(`${ready.length - done.length} of ${ready.length} logs could not be imported.`);
    }
    if (onSuccess) {
      onSuccess(done[0].logId);
      return;
    }
    setQueued(done);
    setStep("running");
  }, [archive.staging_token, canImport, discardStaged, importer, onSuccess, patch, ready, track]);

  if (step === "pick") {
    return (
      <ArchivePicker
        archive={archive}
        selected={selected}
        onChange={setSelected}
        onContinue={continueWithSelection}
        onCancel={cancel}
      />
    );
  }

  if (step === "running") {
    return <BatchImportProgress imports={queued} onReset={onExit} />;
  }

  const busy = step === "importing";

  return (
    <div className="space-y-5">
      <header className="space-y-1">
        <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
          <h2 className="text-base font-semibold">Check each log</h2>
          <span className="text-xs tabular-nums text-muted-foreground">
            {confirmed} of {ready.length} confirmed
            {pending > 0 && ` · reading ${pending} more…`}
          </span>
        </div>
        <p className="text-sm text-muted-foreground">
          Every log keeps its own column mapping. Confirm each one, then import them together.
        </p>
      </header>

      <ul className="space-y-2">
        {logs.map((log) => (
          <ReviewItem
            key={log.member.path}
            log={log}
            open={openPath === log.member.path}
            disabled={busy}
            onToggle={() =>
              setOpenPath((open) => (open === log.member.path ? null : log.member.path))
            }
            onRename={(name) => patch(log.member.path, { name })}
            onChange={(mapping) => patch(log.member.path, { mapping, confirmed: false })}
            onConfirm={() => confirm(log.member.path)}
            onRetry={() => void unpack([log.member.path], runRef.current)}
          />
        ))}
      </ul>

      {failed > 0 && pending === 0 && (
        <p className="flex items-start gap-2 text-xs text-muted-foreground">
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-600 dark:text-amber-400" />
          {failed === 1
            ? "1 log couldn't be read and will be skipped."
            : `${failed} logs couldn't be read and will be skipped.`}
        </p>
      )}

      <div className="flex flex-wrap items-center justify-between gap-2 border-t border-border pt-4">
        <Button variant="ghost" onClick={back} disabled={busy} className="cursor-pointer">
          Back
        </Button>
        <div className="flex gap-2">
          <Button variant="outline" onClick={cancel} disabled={busy} className="cursor-pointer">
            Cancel
          </Button>
          <Button onClick={importAll} disabled={!canImport} className="cursor-pointer gap-2">
            {busy && <Loader2 className="h-4 w-4 animate-spin" />}
            {busy
              ? `Importing ${importing + 1} of ${ready.length}…`
              : ready.length === 1
                ? "Import 1 log"
                : `Import ${ready.length} logs`}
          </Button>
        </div>
      </div>
    </div>
  );
}

function ReviewItem({
  log,
  open,
  disabled,
  onToggle,
  onRename,
  onChange,
  onConfirm,
  onRetry,
}: {
  log: PickedLog;
  open: boolean;
  disabled: boolean;
  onToggle: () => void;
  onRename: (name: string) => void;
  onChange: (mapping: MappingState) => void;
  onConfirm: () => void;
  onRetry: () => void;
}) {
  const nameId = useId();
  const ready = isReady(log);

  return (
    <li
      className={cn(
        "overflow-hidden rounded-lg border transition-colors",
        open ? "border-primary/30" : "border-border",
      )}
    >
      <div className="flex items-center gap-2 px-3 py-2.5">
        <button
          type="button"
          onClick={onToggle}
          disabled={!ready || disabled}
          aria-expanded={ready ? open : undefined}
          className="flex min-w-0 flex-1 cursor-pointer items-center gap-3 text-left disabled:cursor-default"
        >
          <ReviewIcon log={log} />
          <div className="min-w-0 flex-1">
            <div className="truncate text-sm font-medium" title={log.member.path}>
              {log.name || log.member.name}
            </div>
            <ReviewSummary log={log} />
          </div>
          {ready && (
            <ChevronDown
              className={cn(
                "h-4 w-4 shrink-0 text-muted-foreground transition-transform",
                open && "rotate-180",
              )}
            />
          )}
        </button>
        {log.status === "failed" && (
          <Button
            variant="ghost"
            size="sm"
            onClick={onRetry}
            disabled={disabled}
            className="shrink-0 cursor-pointer gap-1.5"
          >
            <RotateCcw className="h-3.5 w-3.5" />
            Retry
          </Button>
        )}
      </div>

      {open && ready && (
        <div className="animate-in fade-in-0 space-y-5 border-t border-border px-3 py-4 duration-200">
          <div className="grid gap-2">
            <Label htmlFor={nameId}>Display name</Label>
            <Input
              id={nameId}
              value={log.name}
              onChange={(e) => onRename(e.target.value)}
              placeholder={displayNameFor(log.member.name)}
              disabled={disabled}
            />
          </div>
          <MappingStep
            probe={log.probe}
            mapping={log.mapping}
            onChange={onChange}
            confirmed={log.confirmed}
            onConfirm={onConfirm}
            aiPending={log.aiPending}
            aiSuggested={log.aiSuggested}
          />
        </div>
      )}
    </li>
  );
}

function ReviewIcon({ log }: { log: PickedLog }) {
  const base = "h-4 w-4 shrink-0";
  if (log.status === "unpacking") return <Loader2 className={cn(base, "animate-spin")} />;
  if (log.status === "failed") return <X className={cn(base, "text-destructive")} />;
  if (!isReady(log)) return <Circle className={cn(base, "text-muted-foreground/40")} />;
  if (log.confirmed) {
    return <Check className={cn(base, "animate-in zoom-in-50 text-chart-2 duration-200")} />;
  }
  if (log.probe.needs_mapping && (missingRequiredRoles(log.mapping).length > 0 || hasLowConfidence(log.mapping))) {
    return <AlertTriangle className={cn(base, "text-amber-600 dark:text-amber-400")} />;
  }
  return <CircleDot className={cn(base, "text-primary")} />;
}

function ReviewSummary({ log }: { log: PickedLog }) {
  if (log.status === "failed") {
    return <div className="line-clamp-2 text-xs text-destructive">{log.error}</div>;
  }
  if (!isReady(log)) {
    return (
      <div className="text-xs text-muted-foreground">
        {log.status === "unpacking" ? "Unpacking and reading columns…" : "Waiting to be unpacked"}
      </div>
    );
  }

  const parts = [formatLabel(log.probe.source_format)];
  if (!log.probe.needs_mapping) parts.push("object-centric, nothing to map");
  else parts.push(`${formatNumber(log.probe.events_sampled)} events sampled`);

  const missing = log.probe.needs_mapping ? missingRequiredRoles(log.mapping) : [];
  let state: string;
  if (log.confirmed) state = "mapping confirmed";
  else if (missing.length > 0) state = `missing ${missing.map((r) => ROLE_LABELS[r] ?? r).join(", ")}`;
  else if (log.probe.needs_mapping && hasLowConfidence(log.mapping)) state = "check the guessed columns";
  else state = "confirm the mapping";

  return (
    <div className="truncate text-xs text-muted-foreground">
      {parts.join(" · ")} · {state}
    </div>
  );
}
