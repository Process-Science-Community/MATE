"use client";

import { useState } from "react";
import { Ban, Loader2, Pencil, RotateCcw, ShieldCheck, Trash2 } from "lucide-react";
import { toast } from "sonner";

import type { DatasetDetail, DatasetVisibility } from "@/lib/api-types";
import { usePurgeDataset, useSetDatasetStatus, useUpdateDataset } from "@/lib/hub-queries";
import { toastError } from "@/lib/toast";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
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
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  DatasetCardForm,
  cardFieldsFromForm,
  cardFormFromDataset,
  validateCardForm,
  type CardFormState,
} from "./dataset-card-form";

/** Curator controls on the dataset page: edit, visibility, takedown. */
export function DatasetAdminPanel({ ds }: { ds: DatasetDetail }) {
  const update = useUpdateDataset(ds.namespace, ds.name);
  const setStatus = useSetDatasetStatus(ds.namespace, ds.name);
  const purge = usePurgeDataset(ds.namespace, ds.name);

  const [editOpen, setEditOpen] = useState(false);
  const [editSeed, setEditSeed] = useState<Partial<CardFormState>>({});
  const [disableOpen, setDisableOpen] = useState(false);
  const [reason, setReason] = useState("");
  const [purgeOpen, setPurgeOpen] = useState(false);
  const [confirm, setConfirm] = useState("");

  const disabled = ds.status === "disabled";
  const purged = ds.revision.purged;

  const onVisibility = async (v: DatasetVisibility) => {
    if (v === ds.visibility) return;
    // Restricted needs a team; any non-private visibility needs a complete card.
    // Hand both cases to the full editor, pre-set to the target visibility.
    const draft = { ...cardFormFromDataset(ds), visibility: v };
    if (Object.keys(validateCardForm(draft, "edit")).length > 0) {
      setEditSeed({ visibility: v });
      setEditOpen(true);
      return;
    }
    try {
      await update.mutateAsync({ visibility: v });
      toast.success(`Visibility set to ${v}`);
    } catch (err) {
      toastError(`Could not change visibility: ${(err as Error).message}`);
    }
  };

  const onDisable = async () => {
    try {
      await setStatus.mutateAsync({ action: "disable", reason: reason.trim() });
      toast.success("Dataset disabled – every copy is now blocked");
      setDisableOpen(false);
      setReason("");
    } catch (err) {
      toastError(`Disable failed: ${(err as Error).message}`);
    }
  };

  const onEnable = async () => {
    try {
      await setStatus.mutateAsync({ action: "enable" });
      toast.success("Dataset enabled");
    } catch (err) {
      toastError(`Enable failed: ${(err as Error).message}`);
    }
  };

  const onPurge = async () => {
    try {
      await purge.mutateAsync(confirm.trim());
      toast.success("Revision files deleted");
      setPurgeOpen(false);
      setConfirm("");
    } catch (err) {
      toastError(`Purge failed: ${(err as Error).message}`);
    }
  };

  return (
    <Card className="gap-4 border-amber-500/30 p-4">
      <div className="flex items-center gap-2">
        <ShieldCheck className="h-4 w-4 text-amber-600 dark:text-amber-400" />
        <h2 className="text-sm font-semibold">Curator</h2>
        <span className="text-xs text-muted-foreground">
          Only dataset curators see this panel.
        </span>
      </div>

      <div className="flex flex-wrap items-end gap-3">
        <div className="space-y-1">
          <Label htmlFor="ds-admin-visibility" className="text-xs text-muted-foreground">
            Visibility
          </Label>
          <Select
            value={ds.visibility}
            onValueChange={(v) => onVisibility(v as DatasetVisibility)}
            disabled={update.isPending}
          >
            <SelectTrigger id="ds-admin-visibility" className="h-8 w-40 text-sm">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="private">Private</SelectItem>
              <SelectItem value="restricted">Restricted</SelectItem>
              <SelectItem value="public">Public</SelectItem>
            </SelectContent>
          </Select>
        </div>
        {ds.visibility === "restricted" && (
          <p className="pb-1.5 text-xs text-muted-foreground">
            Team: <span className="font-medium text-foreground">{ds.audience_team?.name ?? "–"}</span>
          </p>
        )}
        <div className="ml-auto flex flex-wrap gap-2">
          <Button
            variant="outline"
            size="sm"
            className="cursor-pointer gap-1.5"
            onClick={() => {
              setEditSeed({});
              setEditOpen(true);
            }}
          >
            <Pencil className="h-3.5 w-3.5" />
            Edit card
          </Button>
          {disabled ? (
            <Button
              variant="outline"
              size="sm"
              className="cursor-pointer gap-1.5"
              onClick={onEnable}
              disabled={purged || setStatus.isPending}
              title={purged ? "The revision files were purged" : undefined}
            >
              <RotateCcw className="h-3.5 w-3.5" />
              Enable
            </Button>
          ) : (
            <Button
              variant="outline"
              size="sm"
              className="cursor-pointer gap-1.5 border-destructive/40 text-destructive hover:bg-destructive/10 hover:text-destructive"
              onClick={() => setDisableOpen(true)}
            >
              <Ban className="h-3.5 w-3.5" />
              Disable…
            </Button>
          )}
          <Button
            variant="outline"
            size="sm"
            className="cursor-pointer gap-1.5 border-destructive/40 text-destructive hover:bg-destructive/10 hover:text-destructive"
            onClick={() => setPurgeOpen(true)}
            disabled={!disabled || purged}
            title={!disabled ? "Disable the dataset first" : purged ? "Already purged" : undefined}
          >
            <Trash2 className="h-3.5 w-3.5" />
            Purge…
          </Button>
        </div>
      </div>

      {disabled && (
        <p className="rounded-md bg-destructive/10 px-3 py-2 text-xs text-destructive">
          Disabled{ds.disabled_reason ? `: ${ds.disabled_reason}` : ""}. Users can no longer find
          it, and every copy answers &ldquo;withdrawn&rdquo;.
          {purged && " The revision files have been purged; it cannot be re-enabled."}
        </p>
      )}

      {editOpen && (
        <EditCardDialog
          ds={ds}
          seed={editSeed}
          onClose={() => setEditOpen(false)}
          save={update.mutateAsync}
          pending={update.isPending}
        />
      )}

      <Dialog open={disableOpen} onOpenChange={setDisableOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Disable {ds.slug}?</DialogTitle>
            <DialogDescription>
              The dataset disappears from the catalog and every user&apos;s copy is blocked (its
              data returns &ldquo;withdrawn&rdquo;). Running module jobs on copies are cancelled.
              You can re-enable it until the files are purged.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-1">
            <Label htmlFor="ds-disable-reason" className="text-xs text-muted-foreground">
              Reason (shown to users holding a copy)
            </Label>
            <Input
              id="ds-disable-reason"
              value={reason}
              maxLength={500}
              onChange={(e) => setReason(e.target.value)}
              placeholder="e.g. license under review"
            />
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDisableOpen(false)} className="cursor-pointer">
              Cancel
            </Button>
            <Button
              variant="destructive"
              onClick={onDisable}
              disabled={!reason.trim() || setStatus.isPending}
              className="cursor-pointer"
            >
              {setStatus.isPending && <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />}
              Disable
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <AlertDialog open={purgeOpen} onOpenChange={setPurgeOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Purge the files of {ds.slug}?</AlertDialogTitle>
            <AlertDialogDescription>
              Deletes the published Parquet and module results from disk. Copies stay blocked
              for good and the dataset can no longer be enabled. This cannot be undone. Type{" "}
              <span className="font-mono font-semibold text-foreground">{ds.slug}</span> to confirm.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <Input
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
            placeholder={ds.slug}
            className="font-mono"
            aria-label="Type the dataset slug to confirm"
          />
          <AlertDialogFooter>
            <AlertDialogCancel className="cursor-pointer">Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={(e) => {
                e.preventDefault();
                void onPurge();
              }}
              disabled={confirm.trim() !== ds.slug || purge.isPending}
              className="cursor-pointer bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              Purge
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </Card>
  );
}

function EditCardDialog({
  ds,
  seed,
  onClose,
  save,
  pending,
}: {
  ds: DatasetDetail;
  seed: Partial<CardFormState>;
  onClose: () => void;
  save: ReturnType<typeof useUpdateDataset>["mutateAsync"];
  pending: boolean;
}) {
  const [form, setForm] = useState<CardFormState>(() => ({ ...cardFormFromDataset(ds), ...seed }));
  const [touched, setTouched] = useState(false);
  const errors = validateCardForm(form, "edit");
  const valid = Object.keys(errors).length === 0;

  const onSave = async () => {
    setTouched(true);
    if (!valid) return;
    try {
      await save(cardFieldsFromForm(form));
      toast.success("Dataset card saved");
      onClose();
    } catch (err) {
      toastError(`Save failed: ${(err as Error).message}`);
    }
  };

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>Edit {ds.slug}</DialogTitle>
          <DialogDescription>Changes apply to every copy&apos;s attribution right away.</DialogDescription>
        </DialogHeader>
        <DatasetCardForm
          mode="edit"
          value={form}
          onChange={setForm}
          errors={touched ? errors : {}}
        />
        <DialogFooter>
          <Button variant="outline" onClick={onClose} className="cursor-pointer">
            Cancel
          </Button>
          <Button onClick={onSave} disabled={pending || (touched && !valid)} className="cursor-pointer">
            {pending && <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />}
            Save
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
