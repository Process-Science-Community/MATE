"use client";

import { useMemo, useState } from "react";
import { AlertTriangle, ChevronDown, FileMinus, FileText, ShieldAlert } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import type { ArchiveMember, ArchiveProbeResponse } from "@/lib/api-types";
import { cn } from "@/lib/cn";
import { formatBytes } from "@/components/processes/import/file-step";

/** "xes.gz" → "XES", "ocel" → "OCEL" – the format badge on a member row. */
export function formatLabel(format: string | null): string {
  return format ? format.split(".")[0].toUpperCase() : "FILE";
}

/** "BPI/2020/log.xes" → "BPI/2020/" – the folder a member sits in, if any. */
function folderOf(member: ArchiveMember): string {
  const path = member.path.replaceAll("\\", "/");
  const slash = path.lastIndexOf("/");
  return slash > 0 ? path.slice(0, slash + 1) : "";
}

/**
 * The first step for an archive that holds several event logs: which of them
 * to import. Every log starts selected – importing the whole download is the
 * common case – and everything that isn't importable is listed with its
 * reason, so nothing in the archive silently disappears.
 */
export function ArchivePicker({
  archive,
  selected,
  onChange,
  onContinue,
  onCancel,
}: {
  archive: ArchiveProbeResponse;
  selected: Set<string>;
  onChange: (next: Set<string>) => void;
  onContinue: () => void;
  onCancel: () => void;
}) {
  const logs = useMemo(() => archive.members.filter((m) => m.importable), [archive.members]);
  const skipped = useMemo(() => archive.members.filter((m) => !m.importable), [archive.members]);

  const count = logs.filter((m) => selected.has(m.path)).length;
  const selectedBytes = logs.reduce((sum, m) => sum + (selected.has(m.path) ? m.size_bytes : 0), 0);
  const overLimit = selectedBytes > archive.limit_bytes;
  const allChecked = count === logs.length ? true : count > 0 ? "indeterminate" : false;

  const toggle = (path: string, on: boolean) => {
    const next = new Set(selected);
    if (on) next.add(path);
    else next.delete(path);
    onChange(next);
  };

  return (
    <div className="space-y-5">
      <header className="space-y-1">
        <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
          <h2 className="text-base font-semibold">Choose the logs to import</h2>
          <span className="text-xs tabular-nums text-muted-foreground">
            {count} of {logs.length} selected
          </span>
        </div>
        <p className="text-sm text-muted-foreground">
          <span className="font-medium text-foreground">{archive.filename}</span> holds{" "}
          {logs.length} event logs. Each one you pick becomes a process of its own.
        </p>
      </header>

      <div className="overflow-hidden rounded-lg border border-border">
        <label className="flex cursor-pointer items-center gap-3 border-b border-border bg-surface px-3 py-2 text-xs font-medium text-muted-foreground">
          <Checkbox
            checked={allChecked}
            onCheckedChange={(v) => onChange(new Set(v === true ? logs.map((m) => m.path) : []))}
          />
          Select all
          <span className="ml-auto tabular-nums">{formatBytes(selectedBytes)}</span>
        </label>
        <ul className="max-h-80 divide-y divide-border overflow-y-auto">
          {logs.map((member) => (
            <li key={member.path}>
              <label className="flex cursor-pointer items-center gap-3 px-3 py-2.5 transition-colors hover:bg-accent/40">
                <Checkbox
                  checked={selected.has(member.path)}
                  onCheckedChange={(v) => toggle(member.path, v === true)}
                />
                <FileText className="h-4 w-4 shrink-0 text-muted-foreground" />
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm font-medium" title={member.path}>
                    {member.name}
                  </div>
                  {folderOf(member) && (
                    <div className="truncate text-[11px] text-muted-foreground">
                      {folderOf(member)}
                    </div>
                  )}
                </div>
                <span className="shrink-0 rounded border border-border px-1.5 py-0.5 text-[10px] font-medium tracking-wide text-muted-foreground">
                  {formatLabel(member.format)}
                </span>
                <span className="w-16 shrink-0 text-right text-xs tabular-nums text-muted-foreground">
                  {formatBytes(member.size_bytes)}
                </span>
              </label>
            </li>
          ))}
        </ul>
      </div>

      {skipped.length > 0 && <SkippedFiles members={skipped} />}

      {overLimit && (
        <div className="flex items-start gap-2 rounded-md border border-amber-500/30 bg-amber-500/5 px-3 py-2 text-xs text-amber-700 dark:text-amber-400">
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          <span>
            Together these logs unpack to {formatBytes(selectedBytes)} - more than the{" "}
            {formatBytes(archive.limit_bytes)} one upload may unpack to. Pick fewer, or import the
            rest from a second upload.
          </span>
        </div>
      )}

      <div className="flex justify-end gap-2 border-t border-border pt-4">
        <Button variant="outline" onClick={onCancel} className="cursor-pointer">
          Cancel
        </Button>
        <Button
          onClick={onContinue}
          disabled={count === 0 || overLimit}
          className="cursor-pointer"
        >
          {count === 1 ? "Continue with 1 log" : `Continue with ${count} logs`}
        </Button>
      </div>
    </div>
  );
}

/**
 * What the archive holds besides importable logs. Folded away when it's only
 * READMEs and licences; open when a log-like file was refused (encrypted, too
 * large, a suspected zip bomb), since that's one the user may be looking for.
 */
function SkippedFiles({ members }: { members: ArchiveMember[] }) {
  const refusedLogs = members.filter((m) => m.format !== null).length;
  const [open, setOpen] = useState(refusedLogs > 0);

  return (
    <div className="space-y-2">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex cursor-pointer items-center gap-1.5 text-xs font-medium text-muted-foreground transition-colors hover:text-foreground"
      >
        <ChevronDown className={cn("h-3.5 w-3.5 transition-transform", open && "rotate-180")} />
        {members.length === 1
          ? "1 other file won't be imported"
          : `${members.length} other files won't be imported`}
      </button>
      {open && (
        <ul className="animate-in fade-in-0 slide-in-from-top-1 space-y-1 duration-200">
          {members.map((member) => (
            <li
              key={member.path}
              className="flex items-start gap-2 rounded-md px-2 py-1.5 text-xs"
              title={member.path}
            >
              {member.unsafe ? (
                <ShieldAlert className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-600 dark:text-amber-400" />
              ) : (
                <FileMinus className="mt-0.5 h-3.5 w-3.5 shrink-0 text-muted-foreground/60" />
              )}
              <div className="min-w-0">
                <span className="font-medium">{member.name}</span>
                <span className="text-muted-foreground"> - {member.reason}</span>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
