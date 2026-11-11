"use client";

import { useState } from "react";
import { Lock, RotateCcw, SlidersHorizontal } from "lucide-react";

import { SchemaForm } from "@/components/schema-form/schema-form";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";
import {
  useLogModuleConfig,
  useUpdateLogModuleConfig,
  type LogModuleConfig,
} from "@/lib/log-module-config-queries";
import { cn } from "@/lib/cn";
import { toastError } from "@/lib/toast";

type Values = Record<string, unknown>;

/**
 * "Parameters for this log": the module's `ui.scope: dataset` settings, which the
 * user may override for this one event log; everything else follows the module's
 * settings page. Renders nothing for a module without such settings.
 *
 * `onSaved` fires once the save settled and the run gate re-asked `ensure`, so the
 * page can remount the panel onto the new values (a precompute setting instead
 * shows the recompute through the gate).
 */
export function LogModuleParams({
  logId,
  moduleId,
  onSaved,
}: {
  logId: string;
  moduleId: string;
  onSaved: () => void;
}) {
  const { data } = useLogModuleConfig(logId, moduleId);
  const update = useUpdateLogModuleConfig(logId, moduleId);
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState<Values>({});

  if (!data || Object.keys(data.schema.properties ?? {}).length === 0) return null;
  // Admin locks win over per-log values, so a locked key's stored override is moot:
  // neither counted nor sent back on save.
  const locked = data.locked ?? [];
  const saved = Object.fromEntries(
    Object.entries(data.overrides).filter(([key]) => !locked.includes(key)),
  );
  const overridden = Object.keys(saved).length;

  const save = () => {
    setOpen(false);
    update.mutate(draft, {
      onSuccess: onSaved,
      onError: () => toastError("Failed to save the parameters for this log"),
    });
  };

  return (
    <Sheet
      open={open}
      onOpenChange={(next) => {
        if (next) setDraft(saved);
        setOpen(next);
      }}
    >
      <div className="mb-3 flex justify-end">
        <SheetTrigger asChild>
          <Button variant="outline" size="sm" className="cursor-pointer gap-1.5">
            <SlidersHorizontal className="h-3.5 w-3.5" />
            Parameters for this log
            {overridden > 0 && (
              <Badge variant="secondary" aria-label={`${overridden} overridden`}>
                {overridden}
              </Badge>
            )}
          </Button>
        </SheetTrigger>
      </div>
      <SheetContent className="w-full gap-0 sm:max-w-md">
        <ParamsBody
          config={data}
          saved={saved}
          draft={draft}
          setDraft={setDraft}
          onSave={save}
        />
      </SheetContent>
    </Sheet>
  );
}

/** `saved` is the stored overrides minus admin-locked keys - what `draft` starts from. */
function ParamsBody({
  config,
  saved,
  draft,
  setDraft,
  onSave,
}: {
  config: LogModuleConfig;
  saved: Values;
  draft: Values;
  setDraft: (update: (prev: Values) => Values) => void;
  onSave: () => void;
}) {
  const locked = config.locked ?? [];
  const dirty = JSON.stringify(draft) !== JSON.stringify(saved);
  const inherit = (key: string) =>
    setDraft((d) => {
      const next = { ...d };
      delete next[key];
      return next;
    });

  return (
    <>
      <SheetHeader className="border-b border-border pr-10">
        <SheetTitle>Parameters for this log</SheetTitle>
        <SheetDescription>
          Override module settings for this event log only. Inherited fields follow the
          module&apos;s settings page.
        </SheetDescription>
      </SheetHeader>
      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-4">
        <SchemaForm
          schema={config.schema}
          values={{
            ...config.module_values,
            ...draft,
            // A locked key shows the admin value this log actually computes with.
            ...Object.fromEntries(locked.map((key) => [key, config.effective[key]])),
          }}
          // A cleared number input reports `undefined`: treat it as "inherit".
          onChange={(key, value) =>
            value === undefined ? inherit(key) : setDraft((d) => ({ ...d, [key]: value }))
          }
          disabled={(key) => locked.includes(key)}
          fieldExtra={(key, prop) =>
            locked.includes(key) ? (
              <Badge variant="outline" className="font-normal text-muted-foreground">
                <Lock />
                Locked by admin
              </Badge>
            ) : (
              <>
                {key in draft ? (
                  <Badge variant="secondary">Overridden</Badge>
                ) : (
                  <Badge variant="outline" className="font-normal text-muted-foreground">
                    Inherited
                  </Badge>
                )}
                {/* Stays mounted (aria-disabled, not disabled) when inherited, so keyboard
                    focus is not dropped to the sheet root by the reset it triggers. */}
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  className={cn(
                    "h-6 w-6 cursor-pointer",
                    "aria-disabled:cursor-not-allowed aria-disabled:opacity-50",
                  )}
                  title="Reset to the module value"
                  aria-label={`Reset ${prop.title ?? key} to the module value`}
                  aria-disabled={!(key in draft)}
                  onClick={() => key in draft && inherit(key)}
                >
                  <RotateCcw className="h-3.5 w-3.5" />
                </Button>
              </>
            )
          }
        />
      </div>
      <SheetFooter className="flex-row justify-between border-t border-border">
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="cursor-pointer"
          onClick={() => setDraft(() => ({}))}
          disabled={Object.keys(draft).length === 0}
        >
          Reset all to inherit
        </Button>
        <Button
          type="button"
          size="sm"
          className="cursor-pointer"
          onClick={onSave}
          disabled={!dirty}
        >
          Save
        </Button>
      </SheetFooter>
    </>
  );
}
