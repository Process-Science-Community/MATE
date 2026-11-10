"use client";

import Link from "next/link";
import { useRef, useState } from "react";
import { Boxes, Loader2, Pencil, Plus, Trash2, Workflow } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
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
import { EmptyState } from "@/components/empty-state";
import { CardGridSkeleton } from "@/components/skeletons";
import { PageActions, PageContainer, PageHeader } from "@/components/page";
import { StatusBadge } from "@/components/status-badge";
import { api } from "@/lib/api";
import type { LogModel } from "@/lib/api-types";
import { cn } from "@/lib/cn";
import { formatRelative } from "@/lib/format";
import { useProgressRouter } from "@/lib/use-progress-router";
import {
  useCreateFlow,
  useDeleteFlow,
  useFlows,
  useUpdateFlow,
  type FlowDetail,
  type FlowSummary,
} from "@/lib/flow-queries";

import { SEED_GRAPH } from "./flow-graph";

const MODELS: { value: LogModel; label: string; hint: string; icon: typeof Workflow }[] = [
  { value: "case_centric", label: "Case-centric", hint: "XES / CSV logs.", icon: Workflow },
  { value: "object_centric", label: "Object-centric", hint: "OCEL logs.", icon: Boxes },
];

/** `/flows`: the user's flows with their last run; create, rename, delete. */
export function FlowList() {
  const router = useProgressRouter();
  const { data: flows, isLoading } = useFlows();
  const create = useCreateFlow();
  const update = useUpdateFlow();
  const del = useDeleteFlow();

  const [createOpen, setCreateOpen] = useState(false);
  const [newName, setNewName] = useState("");
  const [model, setModel] = useState<LogModel>("case_centric");
  const [renaming, setRenaming] = useState<FlowSummary | null>(null);
  const [renameTo, setRenameTo] = useState("");
  const [deleting, setDeleting] = useState<FlowSummary | null>(null);

  // A ref, not `create.isPending`: a second Enter can land before that re-renders.
  const creating = useRef(false);
  const onCreate = async () => {
    const name = newName.trim();
    if (!name || creating.current) return;
    creating.current = true;
    try {
      const flow = await create.mutateAsync({ name, log_model: model, graph: SEED_GRAPH });
      setCreateOpen(false);
      router.push(`/flows/${flow.id}`);
    } catch {
      toast.error("Could not create the flow");
    } finally {
      creating.current = false;
    }
  };

  const onRename = async () => {
    const flow = renaming;
    const name = renameTo.trim();
    setRenaming(null);
    if (!flow || !name || name === flow.name) return;
    try {
      // PUT replaces the whole flow: carry its current graph over.
      const detail = await api<FlowDetail>(`/api/v1/flows/${flow.id}`);
      await update.mutateAsync({ id: flow.id, name, description: detail.description, graph: detail.graph });
    } catch {
      toast.error("Could not rename the flow");
    }
  };

  const onDelete = () => {
    if (!deleting) return;
    del.mutate(deleting.id, { onError: () => toast.error("Could not delete the flow") });
    setDeleting(null);
  };

  const newButton = (
    <Button
      onClick={() => {
        setNewName("");
        setModel("case_centric");
        setCreateOpen(true);
      }}
    >
      <Plus className="mr-1.5 h-4 w-4" />
      New flow
    </Button>
  );

  return (
    <PageContainer>
      <PageHeader className="justify-end">
        <PageActions>{newButton}</PageActions>
      </PageHeader>

      {isLoading ? (
        <CardGridSkeleton count={3} />
      ) : !flows?.length ? (
        <EmptyState
          icon={Workflow}
          title="No flows yet"
          description="Chain modules into a pipeline – discover a model, convert it, save the result – and run it on any log."
          primaryAction={newButton}
        />
      ) : (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {flows.map((f) => (
            <Card
              key={f.id}
              className="group relative h-full transition-all hover:-translate-y-0.5 hover:border-primary/40 hover:shadow-md"
            >
              <Link href={`/flows/${f.id}`} className="absolute inset-0" aria-label={f.name}>
                <span className="sr-only">{f.name}</span>
              </Link>
              <CardHeader className="pb-2">
                <div className="flex items-start justify-between gap-2">
                  <CardTitle className="truncate text-base">{f.name}</CardTitle>
                  <div className="relative z-10 flex items-center gap-0.5 opacity-0 transition-opacity group-focus-within:opacity-100 group-hover:opacity-100">
                    <Button
                      variant="ghost"
                      size="icon"
                      aria-label={`Rename ${f.name}`}
                      className="h-7 w-7 text-muted-foreground"
                      onClick={() => {
                        setRenameTo(f.name);
                        setRenaming(f);
                      }}
                    >
                      <Pencil className="h-3.5 w-3.5" />
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon"
                      aria-label={`Delete ${f.name}`}
                      className="h-7 w-7 text-muted-foreground hover:text-destructive"
                      onClick={() => setDeleting(f)}
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </Button>
                  </div>
                </div>
              </CardHeader>
              <CardContent className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
                <span>{f.log_model === "object_centric" ? "Object-centric" : "Case-centric"}</span>
                <span>Updated {formatRelative(f.updated_at)}</span>
                {f.last_run ? (
                  <span className="inline-flex items-center gap-1.5">
                    Last run <StatusBadge status={f.last_run.status} className="h-5 px-1.5 text-[10px]" />
                  </span>
                ) : (
                  <span>Never run</span>
                )}
                {f.description && <p className="mt-1 line-clamp-2 w-full">{f.description}</p>}
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>New flow</DialogTitle>
            <DialogDescription>
              Name it and pick its log type. The type is fixed: it decides which modules and
              logs the flow can use.
            </DialogDescription>
          </DialogHeader>
          <Input
            autoFocus
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            placeholder="e.g. Discover and export BPMN"
            aria-label="Flow name"
            onKeyDown={(e) => e.key === "Enter" && void onCreate()}
          />
          <div className="grid grid-cols-2 gap-2">
            {MODELS.map((opt) => {
              const Icon = opt.icon;
              const selected = model === opt.value;
              return (
                <button
                  key={opt.value}
                  type="button"
                  onClick={() => setModel(opt.value)}
                  aria-pressed={selected}
                  className={cn(
                    "flex flex-col gap-1 rounded-lg border p-3 text-left transition-colors",
                    selected
                      ? "border-primary bg-primary/5 ring-1 ring-primary"
                      : "border-border hover:border-primary/40 hover:bg-muted/40",
                  )}
                >
                  <span className="flex items-center gap-1.5 text-sm font-medium">
                    <Icon className="h-4 w-4 shrink-0 text-muted-foreground" />
                    {opt.label}
                  </span>
                  <span className="text-[11px] leading-snug text-muted-foreground">{opt.hint}</span>
                </button>
              );
            })}
          </div>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setCreateOpen(false)}>
              Cancel
            </Button>
            <Button onClick={() => void onCreate()} disabled={!newName.trim() || create.isPending}>
              {create.isPending && <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />}
              Create
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={renaming !== null} onOpenChange={(o) => !o && setRenaming(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Rename flow</DialogTitle>
          </DialogHeader>
          <Input
            autoFocus
            value={renameTo}
            onChange={(e) => setRenameTo(e.target.value)}
            aria-label="Flow name"
            onKeyDown={(e) => e.key === "Enter" && void onRename()}
          />
          <DialogFooter>
            <Button variant="ghost" onClick={() => setRenaming(null)}>
              Cancel
            </Button>
            <Button onClick={() => void onRename()} disabled={!renameTo.trim()}>
              Rename
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <AlertDialog open={deleting !== null} onOpenChange={(o) => !o && setDeleting(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete “{deleting?.name}”?</AlertDialogTitle>
            <AlertDialogDescription>
              Removes the flow and its run history. Artifacts its runs saved stay in your workspace.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={onDelete}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </PageContainer>
  );
}
