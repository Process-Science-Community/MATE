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
import {
  DURATION_OPTION,
  categoryCols,
  numericCols,
} from "@/components/visualizations/charts/chart-common";
import { vizSpecs, vizzesForShape } from "@/lib/visualizations/registry";
import {
  useDatasetCatalog,
  type DashboardItem,
  type DatasetRef,
  type WidgetPropSchema,
} from "@/lib/dashboard-queries";
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
  dataset_ref?: DatasetRef | null;
}

const AUTO = "__auto__";
const NO_DATA = "__none__";

/** Resolve an option's `ui.enumFrom` against the bound dataset. Null when the
 * dataset offers no choices (the option is then hidden). */
function resolveEnum(prop: WidgetPropSchema, dataset?: DatasetEnvelope): WidgetPropSchema | null {
  const from = prop.ui?.enumFrom;
  if (!from) return prop;
  const cols = dataset?.schema.columns ?? [];
  const choices: { id: string; label: string }[] =
    from === "measures"
      ? dataset?.shape === "kpi"
        ? (dataset.data as KpiData).items.map((m) => ({ id: m.key, label: m.label }))
        : []
      : from === "numericColumns"
        ? numericCols(cols)
        : from === "categoryColumns"
          ? categoryCols(cols)
          : from === "datetimeColumns"
            ? cols.filter((c) => c.type === "datetime")
            : cols;
  if (choices.length === 0) return null;
  // A single pick gets a "None" entry so it can be cleared (Radix forbids "" values).
  const none = prop.type === "array" ? [] : [{ id: NO_DATA, label: "None" }];
  const all = [...none, ...choices];
  return { ...prop, enum: all.map((c) => c.id), enumLabels: all.map((c) => c.label) };
}

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

/** Studio only: which of this module's datasets the card reads. Datasets the
 * current viz can't render are listed but disabled, with the reason. */
function DataSelect({
  item,
  moduleId,
  accepts,
  onChange,
}: {
  item: DashboardItem;
  moduleId: string;
  accepts?: DatasetShape[];
  onChange: (patch: VizPatch) => void;
}) {
  const { data: catalog } = useDatasetCatalog();
  const own = (catalog ?? []).filter((d) => d.module_id === moduleId);
  const ref = item.dataset_ref;
  return (
    <div className="space-y-1.5">
      <Label className="text-xs">Data</Label>
      <Select
        value={ref?.module_id === moduleId ? ref.dataset_id : NO_DATA}
        // A different dataset has different columns: drop the old bindings and
        // the options that name its columns/measures (`ui.enumFrom`).
        onValueChange={(v) => {
          const props = vizSpecs.find((s) => s.id === item.viz_id)?.options?.properties ?? {};
          const config = Object.fromEntries(
            Object.entries(item.config ?? {}).filter(
              ([k]) => k !== DURATION_OPTION && !props[k]?.ui?.enumFrom,
            ),
          );
          onChange({
            dataset_ref: v === NO_DATA ? null : { module_id: moduleId, dataset_id: v },
            mapping: {},
            config,
          });
        }}
      >
        <SelectTrigger className="h-8 text-xs">
          <SelectValue placeholder="Choose a dataset" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value={NO_DATA} className="text-xs">
            No data yet (placeholder)
          </SelectItem>
          {own.map((d) => {
            const ok = !accepts || accepts.includes(d.shape);
            return (
              <SelectItem key={d.dataset_id} value={d.dataset_id} disabled={!ok} className="text-xs">
                {d.title}
                {!ok && ` (${d.shape}: this visualization needs ${accepts?.join(" or ")})`}
              </SelectItem>
            );
          })}
        </SelectContent>
      </Select>
      {own.length === 0 && (
        <p className="text-[11px] text-muted-foreground">
          This module declares no datasets yet.
        </p>
      )}
    </div>
  );
}

/**
 * Settings for a generic-viz card: a Title, a visualization picker (filtered to
 * the dataset's shape), per-field column binders (Power-BI style) and the viz's
 * non-field options (reusing the card `config_schema` `Field` renderer).
 *
 * `studioModuleId` (module Studio only, never dashboards) adds the Data select
 * and lists every viz while the card has no dataset.
 */
export function FieldMappingForm({
  item,
  dataset,
  shape,
  onChange,
  studioModuleId,
}: {
  item: DashboardItem;
  dataset?: DatasetEnvelope;
  shape?: DatasetShape;
  onChange: (patch: VizPatch) => void;
  studioModuleId?: string;
}) {
  const unbound = !item.dataset_ref;
  const vizzes = shape ? vizzesForShape(shape) : studioModuleId && unbound ? vizSpecs : [];
  const spec = item.viz_id ? vizzes.find((v) => v.id === item.viz_id) : undefined;
  const columns = dataset?.schema.columns ?? [];
  const mapping = (item.mapping ?? {}) as FieldMapping;

  const setMapping = (key: string, value: string | string[] | undefined) =>
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

      {studioModuleId && (
        <DataSelect
          item={item}
          moduleId={studioModuleId}
          accepts={item.viz_id ? vizSpecs.find((v) => v.id === item.viz_id)?.accepts : undefined}
          onChange={onChange}
        />
      )}

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

      {/* KPI data has no columns to bind; the vizzes auto-bind label/value. */}
      {spec && !unbound && dataset?.shape !== "kpi" && spec.fields.length > 0 && (
        <div className="space-y-3 border-t border-border/60 pt-3">
          {spec.fields.map((f) => {
            if (!f.multiple) {
              return (
                <ColumnBinder
                  key={f.key}
                  field={f}
                  columns={columns}
                  value={currentValue(mapping, f.key)}
                  onChange={(v) => setMapping(f.key, v)}
                />
              );
            }
            // Several columns (e.g. y measures): a multiselect writing string[];
            // none picked means Auto.
            const opts = columns.filter((c) => f.accepts.includes(c.type));
            const v = mapping[f.key];
            return (
              <Field
                key={f.key}
                fieldKey={`map-${f.key}`}
                prop={{
                  type: "array",
                  title: `${f.label}${f.required ? " *" : ""}`,
                  description: opts.length ? "None picked: Auto." : "No matching columns in this dataset.",
                  enum: opts.map((c) => c.id),
                  enumLabels: opts.map((c) => c.label),
                }}
                value={Array.isArray(v) ? v : typeof v === "string" ? [v] : []}
                onChange={(next) =>
                  setMapping(
                    f.key,
                    Array.isArray(next) && next.length ? (next as string[]) : undefined,
                  )
                }
                density="compact"
              />
            );
          })}
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
          {Object.entries(spec.options.properties).map(([key, raw]) => {
            const prop = resolveEnum(raw, dataset);
            if (!prop) return null;
            const single = !!raw.ui?.enumFrom && raw.type !== "array";
            const value = (item.config ?? {})[key];
            return (
              <Field
                key={key}
                fieldKey={key}
                prop={prop}
                value={single ? (value ?? NO_DATA) : value}
                onChange={(v) => setConfig(key, single && v === NO_DATA ? undefined : v)}
              />
            );
          })}
        </div>
      )}
    </div>
  );
}
