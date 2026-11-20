"use client";

import Link from "next/link";
import { Database } from "lucide-react";

import type { SourceDataset } from "@/lib/api-types";
import { datasetHref } from "./dataset-lineage-banner";
import { licenseLabel } from "./license-badge";

/** One-line attribution footer for surfaces built on a dataset copy (dashboards). */
export function DatasetAttribution({ source }: { source: SourceDataset | null | undefined }) {
  if (!source) return null;
  return (
    <div className="flex shrink-0 items-center gap-1.5 truncate border-t border-border px-4 py-1.5 text-[11px] text-muted-foreground sm:px-6 lg:px-8">
      <Database className="h-3 w-3 shrink-0" aria-hidden />
      <span className="truncate" title={source.citation ?? undefined}>
        Data:{" "}
        <Link href={datasetHref(source)} className="font-medium underline-offset-2 hover:underline">
          {source.title}
        </Link>{" "}
        ({source.slug} r{source.revision}) · {licenseLabel(source.license_id, source.license_name)}
        {source.publisher_name ? ` · ${source.publisher_name}` : ""}
        {source.citation ? ` · Cite: ${source.citation}` : ""}
      </span>
    </div>
  );
}
