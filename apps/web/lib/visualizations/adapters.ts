/**
 * Client-side `kind → DatasetEnvelope` normalizers (Phase 1).
 *
 * Existing module routes already return `kind`-tagged JSON and already honor
 * the ambient `X-FF-Event-Filter` header + result-cache variant, so a generic
 * viz card just fetches the route and normalizes the response here - no backend
 * change. The adapters are deliberately defensive (introspect arrays into
 * tables, numeric leaves into KPIs) so an unrecognized-but-well-shaped response
 * still renders. Promoted server-side in Phase 2.
 */

import type {
  ColumnSpec,
  ColumnType,
  DatasetEnvelope,
  DatasetShape,
  GraphData,
  GraphEdge,
  GraphNode,
  KpiData,
  KpiMeasure,
  TableData,
  TreeData,
  TreeNode,
} from "./types";
import { inferFormat } from "@/lib/format";

type Json = Record<string, unknown>;

function isObject(v: unknown): v is Json {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function prettify(key: string): string {
  return key
    // The unit suffix becomes the format (duration, percent), not label text.
    .replace(/_(s|sec|secs|seconds|pct|percent)$/i, "")
    .replace(/[_-]+/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase())
    .trim();
}

const DATE_RE = /^\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2})?/;

function inferType(value: unknown): ColumnType {
  if (typeof value === "boolean") return "boolean";
  if (typeof value === "number") return Number.isInteger(value) ? "integer" : "number";
  if (typeof value === "string") {
    if (DATE_RE.test(value)) return "datetime";
    const n = Number(value);
    if (value.trim() !== "" && Number.isFinite(n)) return Number.isInteger(n) ? "integer" : "number";
    return "string";
  }
  return "string";
}

function roleFor(type: ColumnType): ColumnSpec["role"] {
  if (type === "number" || type === "integer" || type === "duration") return "measure";
  if (type === "datetime") return "time";
  return "dimension";
}

/** Build a typed table from an array of row objects, inferring columns from the
 * union of keys and the type from each column's first non-null value. */
export function tableFromRows(rows: Json[]): TableData {
  const keys: string[] = [];
  const seen = new Set<string>();
  for (const r of rows) {
    for (const k of Object.keys(r)) {
      if (!seen.has(k)) {
        seen.add(k);
        keys.push(k);
      }
    }
  }
  const columns: ColumnSpec[] = keys.map((k) => {
    const sample = rows.find((r) => r[k] != null)?.[k];
    let type = inferType(sample);
    const format = type === "number" || type === "integer" ? inferFormat(k) : undefined;
    if (format === "duration") type = "duration";
    const col: ColumnSpec = { id: k, label: prettify(k), type, role: roleFor(type) };
    if (format === "percent100") col.format = format;
    return col;
  });
  return { columns, rows };
}

/** `{columns: string[], rows: unknown[][]}` → row objects. */
function zipRows(columns: unknown[], rows: unknown[][]): Json[] {
  const ids = columns.map(String);
  return rows.map((r) => Object.fromEntries(ids.map((id, i) => [id, r[i]])));
}

const isMatrix = (v: unknown): v is number[][] =>
  Array.isArray(v) && v.length > 0 && v.every((r) => Array.isArray(r));
const isStrArray = (v: unknown): v is string[] =>
  Array.isArray(v) && v.length > 0 && v.every((x) => typeof x === "string");

/** A dense matrix (`matrix: number[][]`, or `metrics: {name: number[][]}`) with
 * a label array → long rows `{row, col, value | <metric>…}`. Null if absent. */
function matrixRows(obj: Json): Json[] | null {
  let metrics: Record<string, number[][]>;
  if (isMatrix(obj.matrix)) metrics = { value: obj.matrix };
  else if (
    isObject(obj.metrics) &&
    Object.keys(obj.metrics).length > 0 &&
    Object.values(obj.metrics).every(isMatrix)
  )
    metrics = obj.metrics as Record<string, number[][]>;
  else return null;
  const labelKey = ["labels", "activities", "log_ids", "names", "row_labels"].find((k) =>
    isStrArray(obj[k]),
  );
  const rowLabels = labelKey ? (obj[labelKey] as string[]) : [];
  const colLabels = isStrArray(obj.col_labels) ? obj.col_labels : rowLabels;
  const out: Json[] = [];
  Object.values(metrics)[0].forEach((r, i) =>
    r.forEach((_, j) => {
      const row: Json = { row: rowLabels[i] ?? String(i), col: colLabels[j] ?? String(j) };
      for (const [name, m] of Object.entries(metrics)) row[name] = m[i]?.[j] ?? null;
      out.push(row);
    }),
  );
  return out;
}

/** Parallel arrays (a string[] index plus same-length number[] arrays, e.g.
 * `{periods, arrivals, completions}`) → one row per index entry. */
function parallelRows(obj: Json): Json[] | null {
  const indexKey = Object.keys(obj).find((k) => isStrArray(obj[k]));
  if (!indexKey) return null;
  const index = obj[indexKey] as string[];
  const valueKeys = Object.keys(obj).filter((k) => {
    const v = obj[k];
    return (
      k !== indexKey &&
      Array.isArray(v) &&
      v.length === index.length &&
      v.every((x) => x == null || typeof x === "number")
    );
  });
  if (valueKeys.length === 0) return null;
  return index.map((label, i) =>
    Object.fromEntries([[indexKey, label], ...valueKeys.map((k) => [k, (obj[k] as unknown[])[i]])]),
  );
}

/** Table data a producer already shaped (`{columns?, rows}`): typed columns are
 * kept, string columns zip array rows, missing columns are inferred. */
function tableFromData(data: Json): TableData {
  const rows = Array.isArray(data.rows) ? (data.rows as unknown[]) : [];
  const cols = Array.isArray(data.columns) ? (data.columns as unknown[]) : [];
  if (rows.length > 0 && rows.every(Array.isArray)) {
    return tableFromRows(zipRows(cols, rows as unknown[][]));
  }
  const objRows = rows.filter(isObject);
  if (cols.length > 0 && cols.every((c) => isObject(c) && typeof c.id === "string")) {
    return { columns: cols as unknown as ColumnSpec[], rows: objRows };
  }
  return tableFromRows(objRows);
}

/** Collect numeric leaves of an object (one level of nesting) into KPI measures. */
function measuresFrom(obj: Json, prefix = ""): KpiMeasure[] {
  const out: KpiMeasure[] = [];
  for (const [k, v] of Object.entries(obj)) {
    const key = prefix ? `${prefix}.${k}` : k;
    if (typeof v === "number") {
      out.push({ key, label: prettify(k), value: v, format: kpiFormat(k, v) });
    } else if (isObject(v) && typeof v.value_b === "number") {
      // An A/B comparison: B is the value, A its baseline.
      out.push({
        key,
        label: typeof v.label === "string" ? v.label : prettify(k),
        value: v.value_b,
        baseline: typeof v.value_a === "number" ? v.value_a : null,
        better: v.lower_is_better ? "lower" : "higher",
        format: kpiFormat(k, v.value_b),
      });
    } else if (isObject(v)) {
      out.push(...measuresFrom(v, key));
    }
  }
  return out;
}

/** `[{key, value, label?, format?, unit?, baseline?}]` item arrays → measures. */
function measuresFromItems(items: unknown[]): KpiMeasure[] {
  return items.filter(isObject).flatMap((it) => {
    const key = String(it.key ?? it.id ?? "");
    const v = it.value;
    if (!key || !(typeof v === "number" || typeof v === "string" || v === null)) return [];
    const m: KpiMeasure = {
      key,
      label: typeof it.label === "string" ? it.label : prettify(key),
      value: v,
    };
    const fmt =
      typeof it.format === "string"
        ? (it.format as KpiMeasure["format"])
        : typeof v === "number"
          ? kpiFormat(key, v)
          : undefined;
    if (fmt) m.format = fmt;
    if (typeof it.unit === "string") m.unit = it.unit;
    if (typeof it.baseline === "number") m.baseline = it.baseline;
    return [m];
  });
}

function kpiFormat(key: string, value: number): KpiMeasure["format"] {
  const inferred = inferFormat(key);
  if (inferred === "duration" || inferred === "percent100") return inferred;
  const k = key.toLowerCase();
  if (/(fitness|precision|perc|percent|share|ratio|rate)/.test(k) && value >= 0 && value <= 1) {
    return "percent";
  }
  if (/(^n[_.]|count|total|num)/.test(k) && Number.isInteger(value)) return "integer";
  return Number.isInteger(value) ? "integer" : "number";
}

function envelope(
  shape: DatasetShape,
  data: DatasetEnvelope["data"],
  columns: ColumnSpec[] = [],
  meta?: DatasetEnvelope["meta"],
): DatasetEnvelope {
  return { shape, schema: { columns }, data, meta };
}

// ── graph ──────────────────────────────────────────────────────────────────

export function graphFromDfg(json: Json): GraphData {
  const activities = Array.isArray(json.activities) ? (json.activities as Json[]) : [];
  const edges = Array.isArray(json.edges) ? (json.edges as Json[]) : [];
  const nodes: GraphNode[] = activities.map((a) => ({
    id: String(a.id),
    label: String(a.label ?? a.id),
    value: typeof a.frequency === "number" ? a.frequency : undefined,
    kind: "activity",
  }));
  const gEdges: GraphEdge[] = edges.map((e, i) => ({
    id: String(e.id ?? `e${i}`),
    source: String(e.source),
    target: String(e.target),
    value: typeof e.frequency === "number" ? e.frequency : undefined,
    performanceSeconds: typeof e.performance_seconds === "number" ? e.performance_seconds : undefined,
  }));
  return {
    directed: true,
    nodes,
    edges: gEdges,
    start: Object.keys((json.start_activities as Json) ?? {}),
    end: Object.keys((json.end_activities as Json) ?? {}),
  };
}

function graphFromPetri(json: Json): GraphData {
  const places = Array.isArray(json.places) ? (json.places as Json[]) : [];
  const transitions = Array.isArray(json.transitions) ? (json.transitions as Json[]) : [];
  const arcs = Array.isArray(json.arcs) ? (json.arcs as Json[]) : [];
  const nodes: GraphNode[] = [
    ...places.map((p) => ({
      id: String(p.id),
      label: String(p.label ?? ""),
      kind: "place",
      // Absent flags stay undefined so PetriNetViz falls back to the structure.
      initial: typeof p.is_initial === "boolean" ? p.is_initial : undefined,
      final: typeof p.is_final === "boolean" ? p.is_final : undefined,
      tokens: typeof p.tokens === "number" ? p.tokens : undefined,
    })),
    ...transitions.map((t) => ({
      id: String(t.id),
      label: String(t.label ?? t.name ?? ""),
      kind: t.is_invisible ? "silent" : "transition",
    })),
  ];
  const edges: GraphEdge[] = arcs.map((a, i) => ({
    id: String(a.id ?? `a${i}`),
    source: String(a.source),
    target: String(a.target),
    value: typeof a.weight === "number" ? a.weight : undefined,
  }));
  return { directed: true, nodes, edges };
}

/** A generic `{nodes, edges}` graph (value from value|count|frequency). */
function graphFromNodesEdges(json: Json): GraphData {
  const num = (o: Json) =>
    [o.value, o.count, o.frequency].find((v): v is number => typeof v === "number");
  const nodes: GraphNode[] = (json.nodes as unknown[]).filter(isObject).map((n) => ({
    id: String(n.id),
    label: String(n.label ?? n.id),
    value: num(n),
    kind: typeof n.kind === "string" ? n.kind : undefined,
  }));
  const edges: GraphEdge[] = (Array.isArray(json.edges) ? (json.edges as unknown[]) : [])
    .filter(isObject)
    .map((e, i) => ({
      id: String(e.id ?? `e${i}`),
      source: String(e.source),
      target: String(e.target),
      value: num(e),
      label: typeof e.label === "string" ? e.label : undefined,
    }));
  const ids = (v: unknown) =>
    Array.isArray(v) ? v.map(String) : isObject(v) ? Object.keys(v) : undefined;
  return {
    directed: json.directed !== false,
    nodes,
    edges,
    start: ids(json.start),
    end: ids(json.end),
  };
}

// ── tree ─────────────────────────────────────────────────────────────────

function treeFromProcessTree(json: Json): TreeData {
  let counter = 0;
  const walk = (node: Json): TreeNode => {
    const children = Array.isArray(node.children) ? (node.children as Json[]) : [];
    const operator = typeof node.operator === "string" ? node.operator : undefined;
    const label = (node.label as string) ?? operator ?? "·";
    return {
      id: String(node.id ?? `n${counter++}`),
      label,
      kind: operator,
      children: children.map(walk),
    };
  };
  const root = isObject(json.root) ? walk(json.root as Json) : { id: "root", label: "·", children: [] };
  return { root };
}

function treeFromPrefix(json: Json): TreeData {
  const flat = Array.isArray(json.nodes) ? (json.nodes as Json[]) : [];
  const byId = new Map<string, TreeNode>();
  for (const n of flat) {
    byId.set(String(n.id), {
      id: String(n.id),
      label: String(n.label ?? "start"),
      value: typeof n.frequency === "number" ? n.frequency : undefined,
      children: [],
    });
  }
  let root: TreeNode | null = null;
  for (const n of flat) {
    const node = byId.get(String(n.id))!;
    const parent = n.parent != null ? byId.get(String(n.parent)) : null;
    if (parent) parent.children.push(node);
    else root = root ?? node;
  }
  return { root: root ?? { id: "root", label: "start", children: [] } };
}

function treeDefaults(n: unknown): TreeNode {
  if (!isObject(n)) return { id: "root", label: "", children: [] };
  return { ...n, id: String(n.id ?? ""), label: String(n.label ?? ""), children: arrayOr(n.children).map(treeDefaults) } as TreeNode;
}
const arrayOr = (v: unknown): unknown[] => (Array.isArray(v) ? v : []);

// ── public entry ───────────────────────────────────────────────────────────

/** `petri_net@1` → `petri_net`; null/empty → null. */
export function artifactTypeId(artifactType: string | null | undefined): string | null {
  return artifactType ? artifactType.split("@")[0]! : null;
}

/** Normalize a module route's response into the declared shape. `shape` and
 * `artifactType` come from the dataset catalog entry; `json` is the raw route
 * response, kept as the artifact payload when the dataset is typed. */
export function normalize(
  shape: DatasetShape,
  json: unknown,
  artifactType?: string | null,
): DatasetEnvelope {
  const env = normalizeShape(shape, json);
  const type = artifactTypeId(artifactType);
  return type ? { ...env, artifact: { type, payload: json } } : env;
}

function normalizeShape(shape: DatasetShape, json: unknown): DatasetEnvelope {
  const obj: Json = isObject(json) ? json : {};
  const kind = typeof obj.kind === "string" ? obj.kind : "";

  // Envelope passthrough: a route that already returns `{shape, data}` of the
  // declared shape is used as-is (a table's schema is inferred when missing).
  if (obj.shape === shape && isObject(obj.data)) {
    const meta = isObject(obj.meta) ? (obj.meta as DatasetEnvelope["meta"]) : undefined;
    if (shape !== "table") {
      // Default the collections the vizzes iterate, so a partial envelope renders empty instead of throwing.
      const d = obj.data as Json;
      const data =
        shape === "graph"
          ? { ...d, nodes: arrayOr(d.nodes), edges: arrayOr(d.edges) }
          : shape === "kpi"
            ? { ...d, items: arrayOr(d.items) }
            : shape === "tree"
              ? { ...d, root: treeDefaults(d.root) }
              : d;
      return envelope(shape, data as unknown as DatasetEnvelope["data"], [], meta);
    }
    const table = tableFromData(obj.data);
    return envelope("table", table, table.columns, {
      ...meta,
      rowCount: meta?.rowCount ?? table.rows.length,
      note: table.rows.length === 0 ? (meta?.note ?? "No rows.") : meta?.note,
    });
  }

  if (shape === "graph") {
    const g =
      kind === "petri_net"
        ? graphFromPetri(obj)
        : !Array.isArray(obj.activities) && Array.isArray(obj.nodes)
          ? graphFromNodesEdges(obj)
          : graphFromDfg(obj);
    return envelope("graph", g, [], { sourceKind: kind });
  }

  if (shape === "tree") {
    const t = kind === "prefix_tree" ? treeFromPrefix(obj) : treeFromProcessTree(obj);
    return envelope("tree", t, [], { sourceKind: kind });
  }

  if (shape === "kpi") {
    let items: KpiMeasure[] = [];
    let note: string | undefined;
    const list = Array.isArray(obj.items) ? obj.items : Array.isArray(obj.kpis) ? obj.kpis : null;
    if (list) {
      items = measuresFromItems(list);
    } else if (kind === "conformance") {
      if (obj.ran === false || !isObject(obj.kpis)) {
        note = "No conformance run yet - run conformance on this log first.";
      } else {
        items = measuresFrom(obj.kpis as Json);
      }
    } else if (isObject(obj.basic) || isObject(obj.enriched)) {
      // complexity_metrics: basic + enriched numeric leaves.
      items = [
        ...measuresFrom((obj.basic as Json) ?? {}),
        ...(isObject(obj.enriched) ? measuresFrom(obj.enriched as Json, "enriched") : []),
      ];
    } else {
      items = measuresFrom(obj);
    }
    return envelope("kpi", { items } satisfies KpiData, [], { sourceKind: kind, note });
  }

  if (shape === "blob") {
    const value = typeof obj.xml === "string" ? obj.xml : typeof obj.value === "string" ? obj.value : "";
    return envelope("blob", { media: kind === "bpmn" ? "bpmn-xml" : "text", value }, [], { sourceKind: kind });
  }

  // table (default): prefer the known slice, else the first array-of-objects.
  let rows: Json[] = [];
  let fromMatrix = false;
  let note: string | undefined;
  if (kind === "conformance") {
    rows = Array.isArray(obj.per_activity) ? (obj.per_activity as Json[]) : [];
    if (rows.length === 0 && obj.ran === false) note = "No conformance run yet.";
  } else if (Array.isArray(obj.drifts)) {
    rows = obj.drifts as Json[];
  } else if (
    Array.isArray(obj.columns) &&
    Array.isArray(obj.rows) &&
    obj.rows.length > 0 &&
    obj.rows.every(Array.isArray)
  ) {
    rows = zipRows(obj.columns, obj.rows as unknown[][]);
  } else {
    rows = matrixRows(obj) ?? [];
    fromMatrix = rows.length > 0;
    if (rows.length === 0) {
      for (const v of Object.values(obj)) {
        if (Array.isArray(v) && v.length > 0 && isObject(v[0])) {
          rows = v as Json[];
          break;
        }
      }
    }
    if (rows.length === 0) rows = parallelRows(obj) ?? [];
  }
  const table = tableFromRows(rows);
  // Matrix labels ("0", "2023", numeric log ids) are categories, never measures.
  if (fromMatrix)
    for (const c of table.columns)
      if (c.id === "row" || c.id === "col") Object.assign(c, { type: "string", role: roleFor("string") });
  return envelope("table", table, table.columns, {
    sourceKind: kind,
    rowCount: rows.length,
    note: rows.length === 0 ? (note ?? "No rows.") : undefined,
  });
}
