"use client";

import Link from "next/link";
import { useState } from "react";
import { ArrowRight, Loader2, RotateCcw } from "lucide-react";
import { toast } from "sonner";

import { ARTIFACT_TYPES } from "../../../../packages/module-sdk-ts/src/artifacts.generated";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  useImportArtifact,
  useModuleVariant,
  useRunWithArtifact,
  type ArtifactConsumer,
  type ArtifactDetail,
} from "@/lib/artifact-queries";
import { useEventLogs } from "@/lib/queries";

const NO_LOG = "__none__";

function LogSelect({
  id,
  value,
  onChange,
  noneLabel,
}: {
  /** The trigger's id, for a `<Label htmlFor>`; without one it names itself. */
  id?: string;
  value: string;
  onChange: (v: string) => void;
  /** Offer "no log" with this label; omitted = a log is required. */
  noneLabel?: string;
}) {
  const { data: logs } = useEventLogs({ status: "ready" });
  return (
    <Select value={value} onValueChange={onChange}>
      <SelectTrigger id={id} aria-label={id ? undefined : "Event log"} className="w-full">
        <SelectValue placeholder="Choose an event log" />
      </SelectTrigger>
      <SelectContent>
        {noneLabel && <SelectItem value={NO_LOG}>{noneLabel}</SelectItem>}
        {logs?.map((l) => (
          <SelectItem key={l.id} value={l.id}>
            {l.name}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

interface Started extends ArtifactConsumer {
  logId: string;
  variant: string;
}

/** "Use in…": run a module that accepts this artifact's type as a variant with the
 *  artifact bound to the matching input port. */
export function UseInDialog({
  artifact,
  open,
  onOpenChange,
}: {
  artifact: ArtifactDetail;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const run = useRunWithArtifact();
  // Uploads without a log need one to run on; everything else runs on its own log.
  const [logId, setLogId] = useState(artifact.log_id ?? "");
  const [started, setStarted] = useState<Started | null>(null);
  const variant = useModuleVariant(
    started?.logId ?? "",
    started?.module_id ?? "",
    started?.variant ?? null,
  );
  const state = variant.data;

  const choose = (c: ArtifactConsumer, on: string, force = false) =>
    run.mutate(
      { logId: on, moduleId: c.module_id, port: c.port_id, ref: artifact.ref, force },
      {
        onSuccess: (s) => setStarted({ ...c, logId: on, variant: s.variant }),
        onError: (e) => toast.error(e.message),
      },
    );
  const settledBad = state?.status === "failed" || state?.status === "cancelled";

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) setStarted(null);
        onOpenChange(next);
      }}
    >
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Use “{artifact.name}” in…</DialogTitle>
          <DialogDescription>
            Runs the module once more with this {artifact.artifact_type} as its input. The
            module&apos;s default result stays as it is.
          </DialogDescription>
        </DialogHeader>

        {started ? (
          <div className="space-y-3 text-sm">
            <div className="flex items-center gap-2">
              <span className="font-medium">{started.module_name}</span>
              <Badge variant="outline">{state?.status ?? "queued"}</Badge>
              {(!state || state.status === "queued" || state.status === "running") && (
                <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" />
              )}
            </div>
            {settledBad ? (
              <div className="flex items-start gap-2">
                <p className="flex-1 text-destructive">
                  {state.error ?? (state.status === "cancelled" ? "The run was cancelled." : "The run failed.")}
                </p>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={run.isPending}
                  onClick={() => choose(started, started.logId, true)}
                >
                  {run.isPending ? <Loader2 className="animate-spin" /> : <RotateCcw />} Retry
                </Button>
              </div>
            ) : (
              state?.error && <p className="text-destructive">{state.error}</p>
            )}
            {state?.status === "ready" && (
              <p className="text-muted-foreground">
                Done – its results are now in the workspace as variant {state.variant.slice(0, 6)}.
              </p>
            )}
            <Button asChild variant="outline" size="sm">
              <Link href={`/processes/${started.logId}/modules/${started.module_id}`}>
                Open {started.module_name} <ArrowRight className="h-3.5 w-3.5" />
              </Link>
            </Button>
          </div>
        ) : artifact.consumers.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No installed module takes a {artifact.artifact_type} as input. Install one from Module
            Settings to use this artifact.
          </p>
        ) : (
          <div className="space-y-3">
            {!artifact.log_id && (
              <div className="space-y-1.5">
                <Label htmlFor="use-in-log">Run on</Label>
                <LogSelect id="use-in-log" value={logId} onChange={setLogId} />
              </div>
            )}
            <ul className="divide-y rounded-md border">
              {artifact.consumers.map((c) => (
                <li key={`${c.module_id}/${c.port_id}`}>
                  <button
                    type="button"
                    disabled={!logId || run.isPending}
                    onClick={() => choose(c, logId)}
                    className="flex w-full cursor-pointer items-center gap-3 px-3 py-2 text-left text-sm hover:bg-muted/50 disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    <span className="flex-1">
                      <span className="font-medium">{c.module_name}</span>
                      <span className="block text-xs text-muted-foreground">
                        as input “{c.port_id}”{c.required ? " (required)" : ""}
                      </span>
                    </span>
                    {run.isPending && run.variables?.moduleId === c.module_id ? (
                      <Loader2 className="h-4 w-4 animate-spin" />
                    ) : (
                      <ArrowRight className="h-4 w-4 text-muted-foreground" />
                    )}
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}

const JSON_TYPES = Object.entries(ARTIFACT_TYPES).filter(([, t]) => t.storage === "json");

/** Import a JSON artifact (one of the json-stored artifact types) into the workspace. */
export function ImportArtifactDialog({
  open,
  onOpenChange,
  defaultLogId,
  onImported,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  defaultLogId?: string;
  onImported?: (id: string) => void;
}) {
  const create = useImportArtifact();
  const [type, setType] = useState("petri_net");
  const [file, setFile] = useState<File | null>(null);
  const [name, setName] = useState("");
  const [logId, setLogId] = useState(defaultLogId ?? NO_LOG);
  const [problem, setProblem] = useState<string | null>(null);
  const interopHref = logId !== NO_LOG ? `/processes/${logId}/modules/interop` : "/modules/interop";
  // Every way out (Cancel, Escape, outside click, success) starts the next open fresh.
  const setOpen = (next: boolean) => {
    if (!next) {
      setFile(null);
      setName("");
      setProblem(null);
    }
    onOpenChange(next);
  };

  const submit = async () => {
    if (!file) return;
    setProblem(null);
    let payload: unknown;
    try {
      payload = JSON.parse(await file.text());
    } catch {
      setProblem("That file isn't valid JSON.");
      return;
    }
    create.mutate(
      {
        artifact_type: type,
        name: name.trim() || file.name.replace(/\.json$/i, ""),
        log_id: logId === NO_LOG ? null : logId,
        payload,
      },
      {
        onSuccess: (row) => {
          toast.success(`Imported ${row.name}`);
          onImported?.(row.id);
          setOpen(false);
        },
        onError: (e) => setProblem(e.message),
      },
    );
  };

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Import an artifact</DialogTitle>
          <DialogDescription>
            A JSON file in one of the artifact formats, e.g. a Petri net another Mate workspace
            exported. Modules can then use it as input.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          <div className="space-y-1.5">
            <Label htmlFor="artifact-type">Artifact type</Label>
            <Select value={type} onValueChange={setType}>
              <SelectTrigger id="artifact-type" className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {JSON_TYPES.map(([id, t]) => (
                  <SelectItem key={id} value={id}>
                    {id} – {t.description}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="artifact-file">JSON file</Label>
            <Input
              id="artifact-file"
              type="file"
              accept=".json,application/json"
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="artifact-name">Name</Label>
            <Input
              id="artifact-name"
              value={name}
              placeholder={file?.name.replace(/\.json$/i, "") ?? "Defaults to the file name"}
              onChange={(e) => setName(e.target.value)}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="artifact-log">Event log</Label>
            <LogSelect id="artifact-log" value={logId} onChange={setLogId} noneLabel="Not tied to a log" />
          </div>
          {problem && <p className="text-sm text-destructive">{problem}</p>}
          <p className="text-xs text-muted-foreground">
            PNML or BPMN file?{" "}
            <Link href={interopHref} className="underline underline-offset-4 hover:text-foreground">
              Import it through Model Interop
            </Link>
            .
          </p>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => setOpen(false)}>
            Cancel
          </Button>
          <Button onClick={submit} disabled={!file || create.isPending}>
            {create.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
            Import
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
