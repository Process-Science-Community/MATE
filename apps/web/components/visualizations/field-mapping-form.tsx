"use client";

import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Field } from "@/components/dashboards/card-config-form";
import { DURATION_OPTION, numericCols } from "@/components/visualizations/charts/chart-common";
import { vizzesForShape } from "@/lib/visualizations/registry";
import type { DashboardItem } from "@/lib/dashboard-queries";
import type {
  ColumnSpec,
  DatasetEnvelope,
  DatasetShape,
  FieldMapping,
  KpiData,
  VizFieldDef,
} from "@/lib/visualizations/types";

/** Patch shape the card settings emit. Structurally identical to the card's
 * `onUpdate` patch (no shared import needed). */
export interface VizPatch {
  title?: string | null;
  viz_id?: string | null;
  mapping?: Record<string, unknown>;
  config?: Record<string, unknown>;
}

const AUTO = "__auto__";

function currentValue(mapping: FieldMapping, key: string): string | undefined {
  const v = mapping[key];
  if (Array.isArray(v)) return v[0];
  return typeof v === "string" ? v : undefined;
}

function ColumnBinder({
  field,
  columns,
  value,
  onChange,
}: {
  field: VizFieldDef;
  columns: ColumnSpec[];
  value?: string;
  onChange: (v: string | undefined) => void;
}) {
  const opts = columns.filter((c) => field.accepts.includes(c.type));
  return (
    <div className="space-y-1.5">
      <Label className="text-xs">
        {field.label}
        {field.required && <span className="text-destructive"> *</span>}
      </Label>
      <Select
        value={value ?? AUTO}
        onValueChange={(v) => onChange(v === AUTO ? undefined : v)}
      >
        <SelectTrigger className="h-8 text-xs">
          <SelectValue placeholder="Auto" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value={AUTO} className="text-xs">
            Auto
          </SelectItem>
          {opts.map((c) => (
            <SelectItem key={c.id} value={c.id} className="text-xs">
              {c.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      {opts.length === 0 && (
        <p className="text-[11px] text-muted-foreground">No matching columns in this dataset.</p>
      )}
    </div>
  );
}

/**
 * Settings for a generic-viz card: a Title, a visualization picker (filtered to
 * the dataset's shape), per-field column binders (Power-BI style) and the viz's
 * non-field options (reusing the card `config_schema` `Field` renderer).
 */
export function FieldMappingForm({
  item,
  dataset,
  shape,
  onChange,
}: {
  item: DashboardItem;
  dataset?: DatasetEnvelope;
  shape?: DatasetShape;
  onChange: (patch: VizPatch) => void;
}) {
  const vizzes = shape ? vizzesForShape(shape) : [];
  const spec = item.viz_id ? vizzes.find((v) => v.id === item.viz_id) : undefined;
  const columns = dataset?.schema.columns ?? [];
  const mapping = (item.mapping ?? {}) as FieldMapping;

  const setMapping = (key: string, value: string | undefined) =>
    onChange({ mapping: { ...mapping, [key]: value } });
  const setConfig = (key: string, value: unknown) =>
    onChange({ config: { ...(item.config ?? {}), [key]: value } });

  // Numeric columns (table) or measures (KPI vizzes) that can be shown as a duration.
  const durationChoices =
    spec?.id === "table"
      ? numericCols(columns).filter((c) => c.type !== "duration")
      : spec?.group === "kpi" && dataset?.shape === "kpi"
        ? (dataset.data as KpiData).items
            .filter((m) => typeof m.value === "number" && m.format !== "duration")
            .map((m) => ({ id: m.key, label: m.label }))
        : [];

  return (
    <div className="space-y-3.5">
      <div className="space-y-1.5">
        <Label htmlFor="viz-title" className="text-xs">
          Title
        </Label>
        <Input
          id="viz-title"
          value={item.title ?? ""}
          onChange={(e) => onChange({ title: e.target.value })}
          placeholder="Visualization"
          className="h-8 text-xs"
        />
      </div>

      <div className="space-y-1.5">
        <Label className="text-xs">Visualization</Label>
        <Select value={item.viz_id ?? ""} onValueChange={(v) => onChange({ viz_id: v })}>
          <SelectTrigger className="h-8 text-xs">
            <SelectValue placeholder="Choose a visualization" />
          </SelectTrigger>
          <SelectContent>
            {vizzes.map((v) => (
              <SelectItem key={v.id} value={v.id} className="text-xs">
                {v.title}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {spec && spec.fields.length > 0 && (
        <div className="space-y-3 border-t border-border/60 pt-3">
          {spec.fields.map((f) => (
            <ColumnBinder
              key={f.key}
              field={f}
              columns={columns}
              value={currentValue(mapping, f.key)}
              onChange={(v) => setMapping(f.key, v)}
            />
          ))}
        </div>
      )}

      {durationChoices.length > 0 && (
        <div className="border-t border-border/60 pt-3">
          <Field
            fieldKey={DURATION_OPTION}
            prop={{
              type: "array",
              title: "Show as duration",
              description: "Values in seconds, shown as 3h 4m.",
              enum: durationChoices.map((c) => c.id),
              enumLabels: durationChoices.map((c) => c.label),
              ui: { widget: "multiselect" },
            }}
            value={(item.config ?? {})[DURATION_OPTION]}
            onChange={(v) => setConfig(DURATION_OPTION, v)}
          />
        </div>
      )}

      {spec?.options?.properties && (
        <div className="space-y-3 border-t border-border/60 pt-3">
          {Object.entries(spec.options.properties).map(([key, prop]) => (
            <Field
              key={key}
              fieldKey={key}
              prop={prop}
              value={(item.config ?? {})[key]}
              onChange={(v) => setConfig(key, v)}
            />
          ))}
        </div>
      )}
    </div>
  );
}
