"use client";

import { ExternalLink, Scale } from "lucide-react";

import { cn } from "@/lib/cn";
import { useLicenses } from "@/lib/hub-queries";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

export interface LicenseLike {
  id: string;
  name?: string | null;
  url?: string | null;
  open?: boolean;
}

/** Short label for a license id ("cc-by-4.0" → "CC BY 4.0"). */
export function licenseLabel(id: string, name?: string | null): string {
  if (id === "other") return name || "Other license";
  if (id === "unknown") return "Unknown license";
  if (id === "4tu-general-terms") return "4TU terms";
  if (id.startsWith("cc")) {
    return id
      .replace(/^cc0/, "CC0")
      .replace(/^cc-/, "CC-")
      .split("-")
      .map((p, i) => (i === 0 ? p : /^\d/.test(p) ? p : p.toUpperCase()))
      .join(" ");
  }
  return id.toUpperCase().replace(/-(\d)/, " $1");
}

/**
 * License chip with a tooltip carrying the full name + link. Accepts either a
 * full `LicenseOut` or a bare id (e.g. from a log's `source_dataset`), in which
 * case the curated list fills in the name / url / open flag.
 */
export function LicenseBadge({
  license,
  className,
}: {
  license: LicenseLike;
  className?: string;
}) {
  const needsLookup = license.open === undefined || !license.name;
  const { data: licenses } = useLicenses(needsLookup);
  const known = licenses?.find((l) => l.id === license.id);
  const name =
    license.id === "other" ? license.name || "Other license" : license.name || known?.name || license.id;
  const url = license.url ?? (license.id === "other" ? null : known?.url ?? null);
  const open = license.open ?? known?.open ?? false;

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span
          className={cn(
            "inline-flex max-w-full shrink-0 items-center gap-1 rounded-md border px-1.5 py-0.5 text-[11px] font-medium leading-none",
            open
              ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300"
              : "border-border bg-muted/50 text-muted-foreground",
            className,
          )}
        >
          <Scale className="h-3 w-3 shrink-0" aria-hidden />
          <span className="truncate">{licenseLabel(license.id, license.name)}</span>
        </span>
      </TooltipTrigger>
      <TooltipContent className="max-w-xs">
        <div className="space-y-1">
          <p className="font-medium">{name}</p>
          <p className="opacity-80">{open ? "Open license" : "Restricted terms – read before reuse"}</p>
          {url && (
            <a
              href={url}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-1 underline underline-offset-2"
            >
              Read the terms <ExternalLink className="h-3 w-3" />
            </a>
          )}
        </div>
      </TooltipContent>
    </Tooltip>
  );
}
