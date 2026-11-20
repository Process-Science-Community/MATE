"use client";

import dynamic from "next/dynamic";
import Link from "next/link";
import { useState } from "react";
import {
  AlertTriangle,
  Check,
  Database,
  ExternalLink,
  Link2,
  Users,
} from "lucide-react";

import { ApiError } from "@/lib/api";
import type { DatasetDetail } from "@/lib/api-types";
import { formatDateRange, formatNumber, formatRelative } from "@/lib/format";
import { useDataset } from "@/lib/hub-queries";
import { PageContainer } from "@/components/page";
import { EmptyState } from "@/components/empty-state";
import { DetailSkeleton } from "@/components/skeletons";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { LicenseBadge } from "@/components/datasets/license-badge";
import { CiteBlock } from "@/components/datasets/cite-block";
import { DatasetPreview } from "@/components/datasets/dataset-preview";
import { UseDatasetButton } from "@/components/datasets/use-dataset-button";
import { DatasetAdminPanel } from "@/components/datasets/dataset-admin-panel";

const Markdown = dynamic(
  () => import("@/components/modules/guide-markdown").then((m) => m.Markdown),
  { ssr: false },
);

export function DatasetDetailClient({ namespace, name }: { namespace: string; name: string }) {
  const { data: ds, isLoading, isError, error } = useDataset(namespace, name);

  if (isLoading) return <DetailSkeleton />;
  if (isError || !ds) {
    const notFound = error instanceof ApiError && error.status === 404;
    return (
      <PageContainer>
        <EmptyState
          icon={Database}
          title={notFound ? "Dataset not found" : "Couldn't load this dataset"}
          description={
            notFound
              ? "It doesn't exist, or you don't have access to it."
              : ((error as Error)?.message ?? "Unknown error")
          }
          primaryAction={
            <Button asChild variant="outline">
              <Link href="/datasets">Back to datasets</Link>
            </Button>
          }
        />
      </PageContainer>
    );
  }

  return (
    <PageContainer className="space-y-6">
      <Header ds={ds} />
      {ds.viewer.can_curate && <DatasetAdminPanel ds={ds} />}
      {ds.missing_modules.length > 0 && (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2 rounded-lg border border-amber-500/25 bg-amber-500/10 px-3 py-2 text-sm text-amber-700 dark:text-amber-300">
          <AlertTriangle className="h-4 w-4 shrink-0" />
          <span className="flex-1">
            This dataset ships precomputed results for modules you haven&apos;t installed:{" "}
            <span className="font-mono">{ds.missing_modules.join(", ")}</span>.
          </span>
          <Button asChild size="sm" variant="outline" className="h-7 border-amber-500/30 bg-transparent">
            <Link href="/modules">Install required modules</Link>
          </Button>
        </div>
      )}

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_380px]">
        <div className="min-w-0 space-y-6">
          {ds.description_md && (
            <Section title="Description">
              <div className="text-sm">
                <Markdown text={ds.description_md} />
              </div>
            </Section>
          )}
          <Section title="Preview" description="Snapshot taken when the dataset was published.">
            <DatasetPreview preview={ds.revision.preview} />
          </Section>
        </div>
        <div className="min-w-0 space-y-6">
          <Section title="Cite" id="citation">
            <CiteBlock
              citation={ds.citation}
              sourceDoi={ds.source_doi}
              license={ds.license}
              publisherName={ds.publisher_name}
              publisherUrl={ds.publisher_url}
            />
          </Section>
          <Section title="Stats">
            <StatTiles ds={ds} />
          </Section>
          {(ds.source_doi || ds.source_url || ds.retention_until || !ds.allow_external_ai) && (
            <Section title="Source">
              <dl className="space-y-2 text-xs">
                {ds.source_doi && (
                  <Row label="DOI">
                    <a
                      href={`https://doi.org/${ds.source_doi}`}
                      target="_blank"
                      rel="noreferrer"
                      className="inline-flex items-center gap-1 font-mono underline-offset-2 hover:underline"
                    >
                      {ds.source_doi}
                      <ExternalLink className="h-3 w-3" />
                    </a>
                  </Row>
                )}
                {ds.source_url && (
                  <Row label="URL">
                    <a
                      href={ds.source_url}
                      target="_blank"
                      rel="noreferrer"
                      className="inline-flex min-w-0 items-center gap-1 break-all underline-offset-2 hover:underline"
                    >
                      {ds.source_url}
                      <ExternalLink className="h-3 w-3 shrink-0" />
                    </a>
                  </Row>
                )}
                {ds.retention_until && <Row label="Retained until">{ds.retention_until}</Row>}
                {!ds.allow_external_ai && (
                  <Row label="External AI">
                    Not allowed – AI chat, AI guidance and MCP will not send copies of this log to
                    an external provider.
                  </Row>
                )}
              </dl>
            </Section>
          )}
        </div>
      </div>
    </PageContainer>
  );
}

function Header({ ds }: { ds: DatasetDetail }) {
  const [copied, setCopied] = useState(false);
  const copyLink = () => {
    navigator.clipboard
      .writeText(window.location.href.split("#")[0])
      .then(() => {
        setCopied(true);
        setTimeout(() => setCopied(false), 1500);
      })
      .catch(() => null);
  };

  return (
    <header className="flex flex-wrap items-start justify-between gap-4">
      <div className="min-w-0 space-y-2">
        <h1 className="text-2xl font-semibold tracking-tight">{ds.title}</h1>
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 text-xs text-muted-foreground">
          <span className="inline-flex items-center gap-1 font-mono">
            <Link
              href={`/datasets/${encodeURIComponent(ds.namespace)}`}
              className="underline-offset-2 hover:text-foreground hover:underline"
            >
              {ds.namespace}
            </Link>
            /{ds.name}
            <button
              type="button"
              onClick={copyLink}
              aria-label="Copy link to this dataset"
              className="cursor-pointer rounded p-0.5 hover:bg-muted hover:text-foreground"
            >
              {copied ? <Check className="h-3 w-3" /> : <Link2 className="h-3 w-3" />}
            </button>
          </span>
          <LicenseBadge license={ds.license} />
          {ds.publisher_name && (
            <span>
              by{" "}
              {ds.publisher_url ? (
                <a
                  href={ds.publisher_url}
                  target="_blank"
                  rel="noreferrer"
                  className="font-medium text-foreground underline-offset-2 hover:underline"
                >
                  {ds.publisher_name}
                </a>
              ) : (
                <span className="font-medium text-foreground">{ds.publisher_name}</span>
              )}
            </span>
          )}
          <span className="inline-flex items-center gap-1" title="Users with a copy">
            <Users className="h-3 w-3" />
            {formatNumber(ds.uses)} using
          </span>
          <span>updated {formatRelative(ds.updated_at)}</span>
          {ds.visibility !== "public" && (
            <span className="rounded bg-muted px-1.5 py-0.5 uppercase tracking-wide">
              {ds.visibility}
            </span>
          )}
        </div>
        {ds.summary && <p className="max-w-3xl text-sm text-muted-foreground">{ds.summary}</p>}
        {ds.tags.length > 0 && (
          <div className="flex flex-wrap gap-1">
            {ds.tags.map((t) => (
              <Link
                key={t}
                href={`/datasets?q=${encodeURIComponent(t)}`}
                className="rounded-full bg-muted px-2 py-0.5 text-[11px] hover:bg-muted/70"
              >
                {t}
              </Link>
            ))}
          </div>
        )}
      </div>
      <UseDatasetButton ds={ds} />
    </header>
  );
}

function Section({
  title,
  description,
  id,
  children,
}: {
  title: string;
  description?: string;
  id?: string;
  children: React.ReactNode;
}) {
  return (
    <Card id={id} className="scroll-mt-20 gap-3 p-4">
      <div>
        <h2 className="text-sm font-semibold">{title}</h2>
        {description && <p className="mt-0.5 text-xs text-muted-foreground">{description}</p>}
      </div>
      {children}
    </Card>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[96px_1fr] gap-2">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="min-w-0">{children}</dd>
    </div>
  );
}

function formatBytes(bytes: number): string {
  const units = ["B", "KB", "MB", "GB", "TB"];
  let v = bytes;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${v >= 10 || i === 0 ? Math.round(v) : v.toFixed(1)} ${units[i]}`;
}

function StatTiles({ ds }: { ds: DatasetDetail }) {
  const r = ds.revision;
  const span = r.stats.time_span_days;
  const tiles: { label: string; value: string }[] = [
    { label: "Events", value: formatNumber(r.events_count) },
    { label: "Cases", value: formatNumber(r.cases_count) },
    { label: "Activities", value: formatNumber(r.activities_count) },
    { label: "Variants", value: formatNumber(r.variants_count) },
    {
      label: "Time span",
      value: span == null ? "–" : `${formatNumber(span)} day${span === 1 ? "" : "s"}`,
    },
    { label: "Format", value: (r.stats.file_format ?? r.source_format ?? "–").toUpperCase() },
    { label: "Size", value: r.bytes_parquet ? formatBytes(r.bytes_parquet) : "–" },
    { label: "Revision", value: `r${r.number}${r.purged ? " (purged)" : ""}` },
  ];
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-2">
        {tiles.map((t) => (
          <div key={t.label} className="rounded-md border border-border/60 px-3 py-2">
            <p className="text-[10px] uppercase tracking-wide text-muted-foreground">{t.label}</p>
            <p className="truncate text-sm font-medium tabular-nums">{t.value}</p>
          </div>
        ))}
      </div>
      {r.date_min && r.date_max && (
        <p className="text-xs text-muted-foreground">{formatDateRange(r.date_min, r.date_max)}</p>
      )}
      {r.stats.columns && r.stats.columns.length > 0 && (
        <details className="text-xs">
          <summary className="cursor-pointer text-muted-foreground hover:text-foreground">
            {r.stats.columns.length} columns
          </summary>
          <ul className="mt-2 space-y-1">
            {r.stats.columns.map((c) => (
              <li key={c.name} className="flex items-center gap-2">
                <span className="min-w-0 flex-1 truncate font-mono">{c.name}</span>
                <span className="text-muted-foreground">{c.dtype}</span>
                {c.null_ratio != null && c.null_ratio > 0 && (
                  <span className="tabular-nums text-muted-foreground">
                    {Math.round(c.null_ratio * 100)}% empty
                  </span>
                )}
              </li>
            ))}
          </ul>
        </details>
      )}
      {r.canonical_modules.length > 0 && (
        <p className="text-xs text-muted-foreground">
          Precomputed: <span className="font-mono">{r.canonical_modules.join(", ")}</span>
        </p>
      )}
    </div>
  );
}
