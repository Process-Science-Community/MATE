"use client";

import dynamic from "next/dynamic";
import { useMemo, useState } from "react";
import { X } from "lucide-react";

import type { DatasetCardFields, DatasetDetail, DatasetVisibility } from "@/lib/api-types";
import { cn } from "@/lib/cn";
import { useLicenses } from "@/lib/hub-queries";
import { useAdminTeams } from "@/lib/sharing-queries";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";

// The markdown renderer is its own lazy chunk (see guide-markdown.tsx).
const Markdown = dynamic(
  () => import("@/components/modules/guide-markdown").then((m) => m.Markdown),
  { ssr: false },
);

// Client-side mirrors of the server rules (`hub/service.validate_card`). The
// server stays authoritative; these only give instant feedback.
export const SLUG_RE = /^[a-z0-9][a-z0-9-]{1,63}$/;
const TAG_RE = /^[a-z0-9][a-z0-9 _.-]{0,39}$/;
const DOI_RE = /^10\.\d{4,9}\/\S+$/;
const URL_RE = /^https?:\/\/[^\s/?#]+\S*$/i;
const MAX_TAGS = 20;

export interface CardFormState {
  namespace: string;
  name: string;
  title: string;
  summary: string;
  description_md: string;
  license_id: string;
  license_name: string;
  license_url: string;
  citation: string;
  source_url: string;
  source_doi: string;
  publisher_name: string;
  publisher_url: string;
  tagsText: string;
  allow_external_ai: boolean;
  retention_until: string;
  visibility: DatasetVisibility;
  audience_team_id: string;
}

export function emptyCardForm(seed: Partial<CardFormState> = {}): CardFormState {
  return {
    namespace: "",
    name: "",
    title: "",
    summary: "",
    description_md: "",
    license_id: "unknown",
    license_name: "",
    license_url: "",
    citation: "",
    source_url: "",
    source_doi: "",
    publisher_name: "",
    publisher_url: "",
    tagsText: "",
    allow_external_ai: true,
    retention_until: "",
    visibility: "private",
    audience_team_id: "",
    ...seed,
  };
}

export function cardFormFromDataset(ds: DatasetDetail): CardFormState {
  return emptyCardForm({
    namespace: ds.namespace,
    name: ds.name,
    title: ds.title,
    summary: ds.summary ?? "",
    description_md: ds.description_md ?? "",
    license_id: ds.license.id,
    license_name: ds.license.id === "other" ? ds.license.name : "",
    license_url: ds.license.id === "other" ? (ds.license.url ?? "") : "",
    citation: ds.citation ?? "",
    source_url: ds.source_url ?? "",
    source_doi: ds.source_doi ?? "",
    publisher_name: ds.publisher_name ?? "",
    publisher_url: ds.publisher_url ?? "",
    tagsText: ds.tags.join(", "),
    allow_external_ai: ds.allow_external_ai,
    retention_until: ds.retention_until ?? "",
    visibility: ds.visibility,
    audience_team_id: ds.audience_team?.id ?? "",
  });
}

export function parseTags(text: string): string[] {
  const out: string[] = [];
  for (const raw of text.split(",")) {
    const t = raw.trim().toLowerCase();
    if (t && !out.includes(t)) out.push(t);
  }
  return out;
}

const orNull = (s: string) => (s.trim() ? s.trim() : null);

/** Form state → the API's card fields (namespace/name are added by the caller). */
export function cardFieldsFromForm(f: CardFormState): DatasetCardFields {
  const other = f.license_id === "other";
  return {
    title: f.title.trim(),
    summary: f.summary.trim(),
    description_md: orNull(f.description_md),
    license_id: f.license_id,
    license_name: other ? orNull(f.license_name) : null,
    license_url: other ? orNull(f.license_url) : null,
    citation: orNull(f.citation),
    source_url: orNull(f.source_url),
    source_doi: orNull(f.source_doi),
    publisher_name: orNull(f.publisher_name),
    publisher_url: orNull(f.publisher_url),
    tags: parseTags(f.tagsText),
    allow_external_ai: f.allow_external_ai,
    retention_until: f.retention_until || null,
    visibility: f.visibility,
    audience_team_id: f.visibility === "restricted" ? orNull(f.audience_team_id) : null,
  };
}

export type CardErrors = Partial<Record<keyof CardFormState, string>>;

export function validateCardForm(f: CardFormState, mode: "create" | "edit"): CardErrors {
  const e: CardErrors = {};
  if (mode === "create") {
    if (!SLUG_RE.test(f.namespace)) e.namespace = "2–64 chars: a–z, 0–9, '-'; starts with a letter or digit.";
    if (!SLUG_RE.test(f.name)) e.name = "2–64 chars: a–z, 0–9, '-'; starts with a letter or digit.";
  }
  if (!f.title.trim()) e.title = "Required.";
  else if (f.title.length > 200) e.title = "At most 200 characters.";
  if (f.summary.length > 300) e.summary = "At most 300 characters.";
  if (f.license_id === "other") {
    if (!f.license_name.trim()) e.license_name = "Name the license.";
    if (!f.license_url.trim()) e.license_url = "Link the license terms.";
  }
  if (f.visibility !== "private") {
    if (f.license_id === "unknown") e.license_id = "Pick a license before sharing beyond curators.";
    if (!f.citation.trim()) e.citation = "Required before sharing beyond curators.";
    if (!f.publisher_name.trim()) e.publisher_name = "Required before sharing beyond curators.";
  }
  if (f.visibility === "restricted" && !f.audience_team_id) e.audience_team_id = "Pick a team.";
  const tags = parseTags(f.tagsText);
  if (tags.length > MAX_TAGS) e.tagsText = `At most ${MAX_TAGS} tags.`;
  else {
    const bad = tags.find((t) => !TAG_RE.test(t));
    if (bad) e.tagsText = `Invalid tag "${bad}" (a–z, 0–9, space, _ . -; max 40).`;
  }
  if (f.source_doi.trim() && !DOI_RE.test(f.source_doi.trim())) {
    e.source_doi = "A bare DOI, e.g. 10.4121/12721292.";
  }
  // Rendered as links on the dataset page: absolute http(s) only (the API agrees).
  for (const key of ["license_url", "source_url", "publisher_url"] as const) {
    const v = f[key].trim();
    if (v && !URL_RE.test(v)) e[key] = "An absolute http(s) URL, e.g. https://data.4tu.nl/…";
  }
  return e;
}

const TEXTAREA =
  "block w-full rounded-md border border-input bg-background px-3 py-2 text-sm shadow-xs outline-none placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/20";

function Field({
  id,
  label,
  hint,
  error,
  children,
  className,
}: {
  id: string;
  label: string;
  hint?: string;
  error?: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("space-y-1", className)}>
      <Label htmlFor={id} className="text-xs font-medium text-muted-foreground">
        {label}
      </Label>
      {children}
      {error ? (
        <p className="text-[11px] text-destructive">{error}</p>
      ) : hint ? (
        <p className="text-[11px] text-muted-foreground">{hint}</p>
      ) : null}
    </div>
  );
}

const VISIBILITY: { value: DatasetVisibility; label: string; hint: string }[] = [
  { value: "private", label: "Private", hint: "Draft – only dataset curators can see it." },
  { value: "restricted", label: "Restricted", hint: "Members of one team." },
  { value: "public", label: "Public", hint: "Every signed-in MATE user." },
];

/**
 * The dataset card editor, shared by "Publish as dataset" (create) and the
 * curator's "Edit card" dialog (edit; the slug is immutable there).
 */
export function DatasetCardForm({
  mode,
  value,
  onChange,
  errors,
}: {
  mode: "create" | "edit";
  value: CardFormState;
  onChange: (next: CardFormState) => void;
  errors: CardErrors;
}) {
  const { data: licenses } = useLicenses();
  const [preview, setPreview] = useState(false);
  const set = <K extends keyof CardFormState>(k: K, v: CardFormState[K]) =>
    onChange({ ...value, [k]: v });
  const tags = useMemo(() => parseTags(value.tagsText), [value.tagsText]);

  return (
    <div className="space-y-4">
      {mode === "create" && (
        <div className="grid gap-3 sm:grid-cols-2">
          <Field id="ds-namespace" label="Namespace" error={errors.namespace}>
            <Input
              id="ds-namespace"
              value={value.namespace}
              onChange={(e) => set("namespace", e.target.value.toLowerCase())}
              placeholder="pm-course"
              className="h-8 font-mono"
            />
          </Field>
          <Field id="ds-name" label="Name" error={errors.name}>
            <Input
              id="ds-name"
              value={value.name}
              onChange={(e) => set("name", e.target.value.toLowerCase())}
              placeholder="case-study-2026"
              className="h-8 font-mono"
            />
          </Field>
          <p className="text-[11px] text-muted-foreground sm:col-span-2">
            Address:{" "}
            <span className="font-mono text-foreground">
              /datasets/{value.namespace || "namespace"}/{value.name || "name"}
            </span>{" "}
            – cannot be changed later.
          </p>
        </div>
      )}

      <Field id="ds-title" label="Title" error={errors.title}>
        <Input
          id="ds-title"
          value={value.title}
          maxLength={200}
          onChange={(e) => set("title", e.target.value)}
          className="h-8"
        />
      </Field>
      <Field id="ds-summary" label="Summary" error={errors.summary} hint="One line for the catalog card.">
        <Input
          id="ds-summary"
          value={value.summary}
          maxLength={300}
          onChange={(e) => set("summary", e.target.value)}
          className="h-8"
        />
      </Field>

      <div className="space-y-1">
        <div className="flex items-center justify-between">
          <Label htmlFor="ds-description" className="text-xs font-medium text-muted-foreground">
            Description (Markdown)
          </Label>
          <button
            type="button"
            onClick={() => setPreview((p) => !p)}
            className="cursor-pointer text-[11px] text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
          >
            {preview ? "Edit" : "Preview"}
          </button>
        </div>
        {preview ? (
          <div className="min-h-[120px] rounded-md border bg-muted/30 px-3 py-2 text-sm">
            {value.description_md.trim() ? (
              <Markdown text={value.description_md} />
            ) : (
              <p className="text-xs text-muted-foreground">Nothing to preview.</p>
            )}
          </div>
        ) : (
          <textarea
            id="ds-description"
            rows={6}
            value={value.description_md}
            onChange={(e) => set("description_md", e.target.value)}
            className={TEXTAREA}
          />
        )}
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        <Field id="ds-license" label="License" error={errors.license_id}>
          <Select value={value.license_id} onValueChange={(v) => set("license_id", v)}>
            <SelectTrigger id="ds-license" className="h-8 w-full text-sm">
              <SelectValue placeholder="License" />
            </SelectTrigger>
            <SelectContent>
              {(licenses ?? [{ id: value.license_id, name: value.license_id }]).map((l) => (
                <SelectItem key={l.id} value={l.id}>
                  {l.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </Field>
        <Field
          id="ds-retention"
          label="Retain until"
          hint="Optional; shown on the dataset page."
        >
          <Input
            id="ds-retention"
            type="date"
            value={value.retention_until}
            onChange={(e) => set("retention_until", e.target.value)}
            className="h-8"
          />
        </Field>
        {value.license_id === "other" && (
          <>
            <Field id="ds-license-name" label="License name" error={errors.license_name}>
              <Input
                id="ds-license-name"
                value={value.license_name}
                onChange={(e) => set("license_name", e.target.value)}
                className="h-8"
              />
            </Field>
            <Field id="ds-license-url" label="License URL" error={errors.license_url}>
              <Input
                id="ds-license-url"
                type="url"
                value={value.license_url}
                onChange={(e) => set("license_url", e.target.value)}
                className="h-8"
              />
            </Field>
          </>
        )}
      </div>

      <Field
        id="ds-citation"
        label="Citation"
        error={errors.citation}
        hint="Required before the dataset is visible beyond curators."
      >
        <textarea
          id="ds-citation"
          rows={3}
          value={value.citation}
          maxLength={5000}
          onChange={(e) => set("citation", e.target.value)}
          className={cn(TEXTAREA, "font-mono text-xs")}
        />
      </Field>

      <div className="grid gap-3 sm:grid-cols-2">
        <Field id="ds-publisher" label="Publisher" error={errors.publisher_name}>
          <Input
            id="ds-publisher"
            value={value.publisher_name}
            onChange={(e) => set("publisher_name", e.target.value)}
            className="h-8"
          />
        </Field>
        <Field id="ds-publisher-url" label="Publisher URL" error={errors.publisher_url}>
          <Input
            id="ds-publisher-url"
            type="url"
            value={value.publisher_url}
            onChange={(e) => set("publisher_url", e.target.value)}
            className="h-8"
          />
        </Field>
        <Field id="ds-source-url" label="Source URL" error={errors.source_url}>
          <Input
            id="ds-source-url"
            type="url"
            value={value.source_url}
            onChange={(e) => set("source_url", e.target.value)}
            className="h-8"
          />
        </Field>
        <Field id="ds-doi" label="Source DOI" error={errors.source_doi} hint="Bare DOI, e.g. 10.4121/…">
          <Input
            id="ds-doi"
            value={value.source_doi}
            onChange={(e) => set("source_doi", e.target.value)}
            className="h-8 font-mono"
          />
        </Field>
      </div>

      <Field id="ds-tags" label="Tags" error={errors.tagsText} hint="Comma-separated.">
        <Input
          id="ds-tags"
          value={value.tagsText}
          onChange={(e) => set("tagsText", e.target.value)}
          placeholder="loan application, bpi"
          className="h-8"
        />
        {tags.length > 0 && (
          <div className="flex flex-wrap gap-1 pt-1">
            {tags.map((t) => (
              <span
                key={t}
                className="inline-flex items-center gap-1 rounded-full bg-muted px-2 py-0.5 text-[11px]"
              >
                {t}
                <button
                  type="button"
                  aria-label={`Remove tag ${t}`}
                  className="cursor-pointer opacity-60 hover:opacity-100"
                  onClick={() => set("tagsText", tags.filter((x) => x !== t).join(", "))}
                >
                  <X className="h-3 w-3" />
                </button>
              </span>
            ))}
          </div>
        )}
      </Field>

      <div className="flex items-start justify-between gap-4 rounded-md border border-border/60 p-3">
        <div className="space-y-0.5">
          <Label htmlFor="ds-ai" className="text-sm font-medium">
            Allow external AI
          </Label>
          <p className="text-[11px] text-muted-foreground">
            When off, MATE AI chat, AI guidance and MCP refuse to send copies of this log to an
            external provider.
          </p>
        </div>
        <Switch
          id="ds-ai"
          checked={value.allow_external_ai}
          onCheckedChange={(v) => set("allow_external_ai", v)}
        />
      </div>

      <div className="space-y-2">
        <Label className="text-xs font-medium text-muted-foreground">Visibility</Label>
        <RadioGroup
          value={value.visibility}
          onValueChange={(v) => set("visibility", v as DatasetVisibility)}
          className="gap-2"
        >
          {VISIBILITY.map((o) => (
            <label
              key={o.value}
              htmlFor={`ds-vis-${o.value}`}
              className="flex cursor-pointer items-start gap-2 text-sm"
            >
              <RadioGroupItem id={`ds-vis-${o.value}`} value={o.value} className="mt-0.5" />
              <span>
                <span className="font-medium">{o.label}</span>{" "}
                <span className="text-xs text-muted-foreground">– {o.hint}</span>
              </span>
            </label>
          ))}
        </RadioGroup>
        {value.visibility === "restricted" && (
          <TeamSelect
            value={value.audience_team_id}
            onChange={(v) => set("audience_team_id", v)}
            error={errors.audience_team_id}
          />
        )}
      </div>
    </div>
  );
}

function TeamSelect({
  value,
  onChange,
  error,
}: {
  value: string;
  onChange: (v: string) => void;
  error?: string;
}) {
  const { data: teams, isLoading } = useAdminTeams();
  return (
    <Field id="ds-team" label="Audience team" error={error} className="pl-6">
      <Select value={value || undefined} onValueChange={onChange}>
        <SelectTrigger id="ds-team" className="h-8 w-full max-w-sm text-sm">
          <SelectValue placeholder={isLoading ? "Loading teams…" : "Pick a team"} />
        </SelectTrigger>
        <SelectContent>
          {(teams ?? []).map((t) => (
            <SelectItem key={t.id} value={t.id}>
              {t.name} ({t.member_count})
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      {teams && teams.length === 0 && (
        <p className="text-[11px] text-muted-foreground">
          No teams yet – create one under Admin → Teams.
        </p>
      )}
    </Field>
  );
}
