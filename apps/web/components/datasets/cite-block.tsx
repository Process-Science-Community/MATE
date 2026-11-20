"use client";

import { Check, Copy, ExternalLink } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { LicenseBadge, type LicenseLike } from "./license-badge";

/** Citation text with a copy button, plus the license + publisher lines. */
export function CiteBlock({
  citation,
  sourceDoi,
  license,
  publisherName,
  publisherUrl,
}: {
  citation: string | null;
  sourceDoi?: string | null;
  license: LicenseLike;
  publisherName?: string | null;
  publisherUrl?: string | null;
}) {
  const [copied, setCopied] = useState(false);
  const copy = () => {
    if (!citation) return;
    navigator.clipboard
      .writeText(citation)
      .then(() => {
        setCopied(true);
        setTimeout(() => setCopied(false), 1500);
      })
      .catch(() => null);
  };

  return (
    <div className="space-y-3">
      {citation ? (
        <div className="relative rounded-md border bg-muted/40 p-3 pr-11">
          <p className="whitespace-pre-wrap break-words font-mono text-xs leading-relaxed">
            {citation}
          </p>
          <Button
            type="button"
            variant="ghost"
            size="icon"
            onClick={copy}
            aria-label="Copy citation"
            className="absolute right-1.5 top-1.5 h-7 w-7 cursor-pointer"
          >
            {copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
          </Button>
        </div>
      ) : (
        <p className="text-xs text-muted-foreground">No citation provided.</p>
      )}
      {sourceDoi && (
        <p className="text-xs text-muted-foreground">
          Cite the source DOI{" "}
          <a
            href={`https://doi.org/${sourceDoi}`}
            target="_blank"
            rel="noreferrer"
            className="font-mono underline underline-offset-2"
          >
            {sourceDoi}
          </a>
          ; the MATE link is not a persistent identifier.
        </p>
      )}
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 text-xs text-muted-foreground">
        <span className="inline-flex items-center gap-1.5">
          License <LicenseBadge license={license} />
        </span>
        {publisherName && (
          <span>
            Publisher{" "}
            {publisherUrl ? (
              <a
                href={publisherUrl}
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-center gap-0.5 font-medium text-foreground underline-offset-2 hover:underline"
              >
                {publisherName}
                <ExternalLink className="h-3 w-3" />
              </a>
            ) : (
              <span className="font-medium text-foreground">{publisherName}</span>
            )}
          </span>
        )}
      </div>
    </div>
  );
}
