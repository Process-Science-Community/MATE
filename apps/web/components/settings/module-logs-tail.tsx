"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Check, Copy } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import type {
  ModuleLogEventPayload,
  ModuleLogLevel,
  ModuleLogLine,
  ModuleLogs,
} from "@/lib/api-types";
import { cn } from "@/lib/cn";
import { moduleLogsPath, queryKeys } from "@/lib/query-keys";
import { subscribeBus } from "@/lib/ws";

// Most severe first: the order the chips read in.
const LEVELS: ModuleLogLevel[] = ["error", "warning", "info", "debug"];

const LEVEL_LABEL: Record<ModuleLogLevel, string> = {
  error: "Error",
  warning: "Warning",
  info: "Info",
  debug: "Debug",
};

const LEVEL_TEXT: Record<ModuleLogLevel, string> = {
  error: "text-destructive",
  warning: "text-amber-600 dark:text-amber-400",
  info: "text-foreground",
  debug: "text-muted-foreground",
};

// A chip's look while its level is shown.
const LEVEL_CHIP_ON: Record<ModuleLogLevel, string> = {
  error: "border-destructive/40 bg-destructive/10 text-destructive",
  warning: "border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-400",
  info: "border-border bg-muted text-foreground",
  debug: "border-border bg-muted text-muted-foreground",
};

// Debug is the chatty level; start with it hidden (its count still shows on the chip).
const DEFAULT_SHOWN: Record<ModuleLogLevel, boolean> = {
  error: true,
  warning: true,
  info: true,
  debug: false,
};

// What the server keeps per user and module; used until the first page says so.
const FALLBACK_CAPACITY = 1000;
// Safety net under the live stream: catches lines missed while it was reconnecting
// and notices an API restart. Incremental (`after` cursor), so it is cheap.
const POLL_MS = 10_000;

function mergeLines(prev: ModuleLogLine[], incoming: ModuleLogLine[], cap: number): ModuleLogLine[] {
  if (incoming.length === 0) return prev;
  const seen = new Set(prev.map((l) => l.seq));
  const fresh = incoming.filter((l) => !seen.has(l.seq));
  if (fresh.length === 0) return prev;
  const next = [...prev, ...fresh].sort((a, b) => a.seq - b.seq);
  return next.length > cap ? next.slice(-cap) : next;
}

function splitFields(fields: Record<string, unknown>): { rest: string; traceback: string | null } {
  const { traceback, ...others } = fields;
  const rest = Object.entries(others)
    .map(([k, v]) => `${k}=${typeof v === "string" ? v : JSON.stringify(v)}`)
    .join(" ");
  return { rest, traceback: typeof traceback === "string" ? traceback : null };
}

function formatLineForCopy(l: ModuleLogLine): string {
  const ts = new Date(l.ts * 1000).toISOString();
  const { traceback, ...rest } = l.fields;
  const fields = Object.keys(rest).length > 0 ? ` ${JSON.stringify(rest)}` : "";
  const source = l.source === "module" ? "" : ` (${l.source})`;
  const tb = typeof traceback === "string" ? `\n${traceback}` : "";
  return `${ts} [${l.level.toUpperCase()}]${source} ${l.event}${fields}${tb}`;
}

/**
 * The module's recent log lines, filterable by severity (§7.6.2).
 *
 * Reads the tail the API keeps per user and module (`GET /modules/{id}/logs`),
 * so what was logged while this page was closed - or by a sync handler on a
 * worker thread - is there on open. The `module.log.*` bus topic is only the
 * live add-on; lines are de-duplicated against the backfill by `seq`.
 */
export function ModuleLogsTail({ moduleId }: { moduleId: string }) {
  const [lines, setLines] = useState<ModuleLogLine[]>([]);
  const [truncated, setTruncated] = useState(false);
  const [shown, setShown] = useState(DEFAULT_SHOWN);
  const [copied, setCopied] = useState(false);

  const capacity = useRef(FALLBACK_CAPACITY);
  // Highest `last_seq` a *page* reported. Live frames never advance it, so a line
  // missed during a stream reconnect is still fetched by the next poll.
  const cursor = useRef(0);

  const page = useQuery({
    queryKey: queryKeys.moduleLogs(moduleId),
    queryFn: () =>
      api<ModuleLogs>(
        moduleLogsPath(moduleId, { limit: FALLBACK_CAPACITY, after: cursor.current }),
      ),
    refetchInterval: POLL_MS,
    // The cursor lives in this mount, so a cached page from an earlier one is useless.
    staleTime: 0,
    gcTime: 0,
  });

  const { data: pageData, refetch } = page;
  useEffect(() => {
    if (!pageData) return;
    if (pageData.last_seq < cursor.current) {
      // The API restarted: its sequence started over, so this view is stale.
      cursor.current = 0;
      setLines([]);
      setTruncated(false);
      void refetch();
      return;
    }
    cursor.current = pageData.last_seq;
    capacity.current = pageData.capacity;
    setLines((prev) => mergeLines(prev, pageData.lines, pageData.capacity));
    if (pageData.truncated) setTruncated(true);
  }, [pageData, refetch]);

  useEffect(() => {
    const sub = subscribeBus<ModuleLogEventPayload>(["module.log.*"], (env) => {
      const p = env.payload;
      if (p.module_id !== moduleId) return;
      const line: ModuleLogLine = {
        seq: p.seq,
        ts: p.ts ?? env.ts,
        level: env.topic.split(".").pop() as ModuleLogLevel,
        event: p.event,
        fields: p.fields ?? {},
        source: p.source ?? "module",
        log_id: p.log_id ?? null,
        job_id: p.job_id ?? null,
      };
      setLines((prev) => mergeLines(prev, [line], capacity.current));
    });
    return () => sub.close();
  }, [moduleId]);

  const counts = useMemo(() => {
    const c: Record<ModuleLogLevel, number> = { error: 0, warning: 0, info: 0, debug: 0 };
    for (const l of lines) c[l.level] += 1;
    return c;
  }, [lines]);
  const visible = useMemo(() => lines.filter((l) => shown[l.level]), [lines, shown]);

  // Sticky-bottom tail: follow new lines only while the reader is parked at the
  // bottom; scrolling up to read history stops the follow until they scroll back.
  const boxRef = useRef<HTMLDivElement>(null);
  const stick = useRef(true);
  const onScroll = () => {
    const el = boxRef.current;
    if (el) stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
  };
  useEffect(() => {
    const el = boxRef.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [visible]);

  const onCopy = async () => {
    if (visible.length === 0) return;
    try {
      await navigator.clipboard.writeText(visible.map(formatLineForCopy).join("\n"));
      setCopied(true);
      // Brief visual confirmation, then revert to the copy icon.
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      toast.error("Failed to copy logs to clipboard");
    }
  };

  const hidden = lines.length - visible.length;

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div role="group" aria-label="Filter by severity" className="flex flex-wrap gap-1.5">
          {LEVELS.map((level) => {
            const on = shown[level];
            return (
              <button
                key={level}
                type="button"
                aria-pressed={on}
                onClick={() => setShown((s) => ({ ...s, [level]: !s[level] }))}
                className={cn(
                  "inline-flex cursor-pointer items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-medium transition-colors",
                  on
                    ? LEVEL_CHIP_ON[level]
                    : "border-dashed border-border bg-transparent text-muted-foreground/70 hover:bg-muted/60",
                )}
              >
                {LEVEL_LABEL[level]}
                <span className="tabular-nums opacity-70">{counts[level]}</span>
              </button>
            );
          })}
        </div>
        <Button
          variant="ghost"
          size="sm"
          className="cursor-pointer gap-1.5 text-xs"
          onClick={onCopy}
          disabled={visible.length === 0}
        >
          {copied ? (
            <>
              <Check className="h-3.5 w-3.5" />
              Copied
            </>
          ) : (
            <>
              <Copy className="h-3.5 w-3.5" />
              Copy
            </>
          )}
        </Button>
      </div>

      <div
        ref={boxRef}
        onScroll={onScroll}
        className="max-h-72 min-h-24 overflow-auto rounded-md border bg-muted/30 px-3 py-2 font-mono text-[11px] leading-relaxed"
      >
        {page.isPending ? (
          <div className="text-muted-foreground">Loading…</div>
        ) : page.isError && lines.length === 0 ? (
          <div className="text-destructive">Could not load the logs.</div>
        ) : lines.length === 0 ? (
          <div className="text-muted-foreground">
            No log lines yet. Open the module on a process or run one of its jobs and its output
            shows up here.
          </div>
        ) : visible.length === 0 ? (
          <div className="text-muted-foreground">
            Every line is hidden by the severity filter — {hidden}{" "}
            {hidden === 1 ? "line" : "lines"} held back.
          </div>
        ) : (
          <ul className="space-y-0.5">
            {visible.map((l) => {
              const { rest, traceback } = splitFields(l.fields);
              return (
                <li key={l.seq}>
                  <div className="flex items-baseline gap-2">
                    <span className="shrink-0 tabular-nums text-muted-foreground">
                      {new Date(l.ts * 1000).toLocaleTimeString()}
                    </span>
                    <span
                      className={cn(
                        "w-14 shrink-0 text-[10px] font-semibold uppercase",
                        LEVEL_TEXT[l.level],
                      )}
                    >
                      {l.level}
                    </span>
                    <span className="min-w-0 whitespace-pre-wrap break-words">
                      {l.source !== "module" && (
                        <span className="mr-1.5 rounded bg-muted px-1 text-[9px] uppercase text-muted-foreground">
                          {l.source}
                        </span>
                      )}
                      <span className={LEVEL_TEXT[l.level]}>{l.event}</span>
                      {rest && (
                        <span className="line-clamp-3 break-all text-muted-foreground"> {rest}</span>
                      )}
                    </span>
                  </div>
                  {traceback && (
                    <details className="ml-[4.5rem] mt-0.5 text-muted-foreground">
                      <summary className="cursor-pointer select-none">Traceback</summary>
                      <pre className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap break-words">
                        {traceback}
                      </pre>
                    </details>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </div>

      <div className="text-xs text-muted-foreground">
        Showing {visible.length} of {lines.length} {lines.length === 1 ? "line" : "lines"}
        {hidden > 0 ? ` · ${hidden} hidden by the filter` : ""}
        {truncated ? ` · only the latest ${capacity.current} are kept` : ""} · live
      </div>
    </div>
  );
}
