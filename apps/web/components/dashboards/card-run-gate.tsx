"use client";

import { useCallback } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { ModuleRunGate } from "@/components/processes/module-run-gate";
import { useModules } from "@/lib/queries";

/**
 * Holds a dashboard card back until its module has computed for the board's log.
 *
 * A board is an explicit request to see this data, so its cards start the runs
 * they need, exactly like opening the module page - with a compact progress state
 * in the card instead of a page. Two cases stay hands-off:
 *   - a **shared** board (`isOwner` false): a viewer must never start work on the
 *     owner's log, so the card renders as before and shows whatever is cached;
 *   - a card with no module behind it, or no bound log.
 *
 * A module with nothing to precompute (`precompute_deps` empty) is not gated.
 */
export function CardRunGate({
  logId,
  moduleId,
  isOwner,
  children,
}: {
  logId: string | null;
  moduleId: string | null | undefined;
  isOwner: boolean;
  children: React.ReactNode;
}) {
  // Inside a board this resolves against the board's own widget client, which is
  // fine: one cached list per board, shared by every card.
  const { data: modules } = useModules(logId);
  const widgetClient = useQueryClient();

  // The widget client outlives the gate, so after a fresh compute drop whatever
  // this module's cards cached against the previous data before they remount.
  const onComputed = useCallback(() => {
    if (!moduleId) return;
    void widgetClient.invalidateQueries({
      predicate: (q) => q.queryKey.includes(moduleId),
    });
  }, [widgetClient, moduleId]);

  if (!logId || !moduleId || !isOwner) return <>{children}</>;
  const deps = modules?.find((m) => m.id === moduleId)?.precompute_deps ?? [];
  return (
    <ModuleRunGate
      logId={logId}
      moduleId={moduleId}
      deps={deps}
      variant="card"
      onComputed={onComputed}
    >
      {children}
    </ModuleRunGate>
  );
}
