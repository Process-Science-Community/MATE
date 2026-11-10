"use client";

import dynamic from "next/dynamic";
import { useSession } from "next-auth/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { History } from "lucide-react";
import { toast } from "sonner";

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { api } from "@/lib/api";
import type { ModuleVersions } from "@/lib/api-types";
import { formatRelative } from "@/lib/format";
import { toastError } from "@/lib/toast";

// The changelog renders with the "How to use" guide's markdown chunk, loaded on demand.
const GuideBlocks = dynamic(() => import("@/components/modules/guide-markdown"), {
  ssr: false,
  loading: () => <Skeleton className="h-16 w-full" />,
});

const versionsKey = (moduleId: string) => ["modules", moduleId, "versions"] as const;

/** The module detail page's "Versions" card: the loaded version, the install history
 *  of an uploaded module, its `CHANGELOG.md`, and – for an admin, when a previous
 *  version is kept – "Roll back". Renders nothing for a module with none of those
 *  (a bundled one without a changelog). */
export function ModuleVersionsCard({ moduleId }: { moduleId: string }) {
  const qc = useQueryClient();
  const { data: session } = useSession();
  const isAdmin = session?.user?.isAdmin === true;
  const { data, isLoading } = useQuery({
    queryKey: versionsKey(moduleId),
    queryFn: () => api<ModuleVersions>(`/api/v1/modules/${moduleId}/versions`),
  });
  // Not optimistic: the server builds, import-checks and swaps folders, and keeps the
  // current version when any step fails - the card shows what actually loaded.
  const rollback = useMutation({
    mutationFn: () => api(`/api/v1/admin/modules/${moduleId}/rollback`, { method: "POST" }),
    onSuccess: () => toast.success(`Rolled back to ${data?.previous_version ?? "the previous version"}`),
    onError: (e) => toastError(`Rollback failed: ${(e as Error).message}`),
    // The manifest (version in the header), the lists and this card all moved.
    onSettled: () => qc.invalidateQueries({ queryKey: ["modules"] }),
  });

  if (isLoading) return <Skeleton className="h-24 w-full" />;
  if (!data) return null;
  const canRollback = isAdmin && data.previous_available;
  if (!data.history.length && !data.changelog && !canRollback) return null;

  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <CardTitle className="text-base">Versions</CardTitle>
          {canRollback && (
            <AlertDialog>
              <AlertDialogTrigger asChild>
                <Button
                  size="sm"
                  variant="outline"
                  className="cursor-pointer gap-1.5"
                  disabled={rollback.isPending}
                >
                  <History className="h-3.5 w-3.5" />
                  {rollback.isPending
                    ? "Rolling back…"
                    : `Roll back to ${data.previous_version ?? "previous"}`}
                </Button>
              </AlertDialogTrigger>
              <AlertDialogContent>
                <AlertDialogHeader>
                  <AlertDialogTitle>
                    Roll back to {data.previous_version ?? "the previous version"}?
                  </AlertDialogTitle>
                  <AlertDialogDescription>
                    Every user of <code>{moduleId}</code> gets version{" "}
                    {data.previous_version ?? "the previous version"} instead of{" "}
                    {data.current.version}. Results it computed turn stale and recompute
                    on the next open. Rolling back again returns to {data.current.version}.
                  </AlertDialogDescription>
                </AlertDialogHeader>
                <AlertDialogFooter>
                  <AlertDialogCancel className="cursor-pointer">Cancel</AlertDialogCancel>
                  <AlertDialogAction className="cursor-pointer" onClick={() => rollback.mutate()}>
                    Roll back
                  </AlertDialogAction>
                </AlertDialogFooter>
              </AlertDialogContent>
            </AlertDialog>
          )}
        </div>
      </CardHeader>
      <CardContent className="space-y-4 text-sm">
        <div className="flex flex-wrap items-baseline gap-2">
          <span className="text-xs uppercase tracking-wide text-muted-foreground">Current</span>
          <span className="font-medium">{data.current.version}</span>
          {data.current.content_hash && (
            <code className="text-[11px] text-muted-foreground" title={data.current.content_hash}>
              {data.current.content_hash.slice(0, 12)}
            </code>
          )}
        </div>
        {data.history.length > 0 && (
          <div>
            <div className="text-xs uppercase tracking-wide text-muted-foreground">History</div>
            <ol className="mt-1 divide-y rounded-md border">
              {data.history.map((h, i) => (
                <li key={`${h.installed_at}-${i}`} className="flex items-center gap-2 px-3 py-1.5 text-xs">
                  <span className="font-medium">{h.version}</span>
                  {h.content_hash && (
                    <code className="text-[11px] text-muted-foreground" title={h.content_hash}>
                      {h.content_hash.slice(0, 12)}
                    </code>
                  )}
                  <span className="ml-auto text-muted-foreground" title={h.installed_at}>
                    installed {formatRelative(h.installed_at)}
                  </span>
                </li>
              ))}
            </ol>
          </div>
        )}
        {data.changelog && (
          <div>
            <div className="text-xs uppercase tracking-wide text-muted-foreground">Changelog</div>
            <div className="mt-1 max-h-80 overflow-y-auto rounded-md border px-3 py-2">
              <GuideBlocks blocks={[{ kind: "markdown", text: data.changelog }]} />
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
