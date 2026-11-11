/**
 * Conformance-overlay decoration for the BPMN canvas.
 *
 * Adapted from the discovery module's frequency heatmap. Instead of colouring
 * tasks by how *often* they run, it colours them by how much they *deviate*
 * from the reference model: green = conforming, a red ramp = increasing
 * deviation, amber-dashed = a model task whose label never appears in the log
 * (the #1 conformance gotcha - a naming mismatch, not a real deviation).
 *
 * The colours live in `colors.ts` (shared with the legend and the widgets).
 *
 * Label matching is EXACT after canonicalisation (trim + collapse whitespace +
 * case-fold), mirroring the backend. There is deliberately no fuzzy matching:
 * "Aproval" never matches "Approval".
 *
 * Everything is view-only: it decorates via diagram-js *markers* (CSS classes)
 * and *overlays* (floating HTML), never via `modeling.*`, so it never marks the
 * model dirty and is excluded from export.
 */

import { BPMN_BLACK } from "@/components/visualizations/artifacts/bpmn";

import { CONF_COLORS, DEV_BUCKETS, devColor } from "./colors";
import type { PerActivityDeviation } from "./types";

export type BpmnModelerLike = { get<T = unknown>(name: string): T };

interface BElement {
  id: string;
  type: string;
  businessObject?: { name?: string; $type?: string };
}
interface ElementRegistry {
  getAll(): BElement[];
}
interface Overlays {
  add(elementId: string, type: string, opts: unknown): string;
  remove(filter: { type?: string; element?: string }): void;
}
interface Canvas {
  addMarker(id: string, cls: string): void;
  removeMarker(id: string, cls: string): void;
}

/**
 * Canonical key for activity-label matching: trim, collapse internal
 * whitespace, case-fold. Matching stays EXACT on this key - whitespace/case
 * variants unify, but one wrong letter is a different activity ("Aproval"
 * never matches "Approval"). Mirrors the backend's `_canon_label`; there is
 * deliberately no fuzzy matching.
 */
export function canonLabel(s: string): string {
  return s.replace(/\s+/g, " ").trim().toLowerCase();
}

export interface DeviationInfo {
  deviations: number;
  logMoves: number;
  modelMoves: number;
  matched: boolean;
}

export interface DeviationMaps {
  byActivity: Map<string, DeviationInfo>;
  /** Fallback keyed by `canonLabel` - exact-after-normalisation, never fuzzy. */
  byCanonical: Map<string, DeviationInfo>;
  maxDeviation: number;
}

export function buildDeviationMaps(
  perActivity: PerActivityDeviation[] | undefined,
): DeviationMaps {
  const byActivity = new Map<string, DeviationInfo>();
  const byCanonical = new Map<string, DeviationInfo>();
  let maxDeviation = 0;
  for (const a of perActivity ?? []) {
    const info: DeviationInfo = {
      deviations: a.deviations,
      logMoves: a.log_moves,
      modelMoves: a.model_moves,
      matched: a.matched,
    };
    byActivity.set(a.activity, info);
    // First wins on canonical collisions (rows arrive sorted worst-first).
    const key = canonLabel(a.activity);
    if (!byCanonical.has(key)) byCanonical.set(key, info);
    if (a.deviations > maxDeviation) maxDeviation = a.deviations;
  }
  return { byActivity, byCanonical, maxDeviation };
}

/** Exact lookup first, then trim/case-normalised exact - never fuzzy. */
function lookupDeviation(maps: DeviationMaps, name: string): DeviationInfo | undefined {
  return maps.byActivity.get(name) ?? maps.byCanonical.get(canonLabel(name));
}

function isTask(el: BElement): boolean {
  return /Task$/.test(el.type) || el.type === "bpmn:SubProcess";
}
function nameOf(el: BElement | undefined): string | undefined {
  const n = el?.businessObject?.name;
  return n && n.length > 0 ? n : undefined;
}
function bucketOf(ratio: number): number {
  if (ratio <= 0) return 0;
  return Math.max(1, Math.min(DEV_BUCKETS, Math.ceil(ratio * DEV_BUCKETS)));
}

export interface ConformanceDecorOptions {
  maps: DeviationMaps;
  heatmap: boolean;
  labels: boolean;
  /** Alignments expose log/model moves separately; show them as `+L −M`. */
  alignments: boolean;
}

const DEV_CLASSES = Array.from({ length: DEV_BUCKETS }, (_, i) => `ff-conf-dev-${i + 1}`);
const STATE_CLASSES = ["ff-conf-ok", "ff-conf-unmatched"];

function clearDecoration(modeler: BpmnModelerLike): void {
  const registry = modeler.get<ElementRegistry>("elementRegistry");
  const overlays = modeler.get<Overlays>("overlays");
  const canvas = modeler.get<Canvas>("canvas");
  overlays.remove({ type: "ff-conf" });
  for (const el of registry.getAll()) {
    for (const c of DEV_CLASSES) canvas.removeMarker(el.id, c);
    for (const c of STATE_CLASSES) canvas.removeMarker(el.id, c);
  }
}

/** Re-apply the conformance overlay from scratch. Idempotent. */
export function applyConformanceOverlay(
  modeler: BpmnModelerLike,
  opts: ConformanceDecorOptions,
): void {
  const { maps, heatmap, labels, alignments } = opts;
  clearDecoration(modeler);

  const registry = modeler.get<ElementRegistry>("elementRegistry");
  const overlays = modeler.get<Overlays>("overlays");
  const canvas = modeler.get<Canvas>("canvas");

  for (const el of registry.getAll()) {
    if (!isTask(el)) continue;
    const name = nameOf(el);
    if (!name) continue;
    const info = lookupDeviation(maps, name);
    if (info === undefined) continue;

    if (!info.matched) {
      if (heatmap) canvas.addMarker(el.id, "ff-conf-unmatched");
      if (labels) {
        overlays.add(el.id, "ff-conf", {
          position: { top: -10, right: 12 },
          html: `<div class="ff-conf-badge ff-conf-badge-warn" title="In the model but never recorded in the log - likely an activity-name mismatch">not in log</div>`,
        });
      }
      continue;
    }

    if (info.deviations <= 0) {
      if (heatmap) canvas.addMarker(el.id, "ff-conf-ok");
      continue;
    }

    if (heatmap) {
      const ratio = maps.maxDeviation > 0 ? info.deviations / maps.maxDeviation : 0;
      const b = bucketOf(ratio);
      if (b > 0) canvas.addMarker(el.id, `ff-conf-dev-${b}`);
    }
    if (labels) {
      const text = alignments
        ? `+${info.logMoves} −${info.modelMoves}`
        : `${info.deviations}`;
      const title = alignments
        ? `${info.logMoves} in the log but not allowed by the model (+) · ${info.modelMoves} required by the model but skipped in the log (−)`
        : `${info.deviations} deviation(s): the recorded process differs from the model at this step`;
      overlays.add(el.id, "ff-conf", {
        position: { top: -10, right: 12 },
        html: `<div class="ff-conf-badge" title="${title}">${text}</div>`,
      });
    }
  }
}

/**
 * `BpmnView` themes the diagram via CSS variables (dark mode = app tokens).
 * Decoration markers that paint a *light* fill in both themes (the deviation
 * ramp plus the ok/unmatched states - their lightness range is tuned to keep
 * dark labels readable) must keep dark labels, so they re-assert bpmn-js'
 * default label colour.
 */
function themeCss(litShapeClasses: string[]): string {
  const litLabels = litShapeClasses
    .map((c) => `.dark .djs-element.${c} .djs-visual text`)
    .join(",");
  return `${litLabels}{fill:${BPMN_BLACK}!important;}`;
}

const STYLE_ID = "ff-conformance-styles";

/** Idempotently inject the overlay stylesheet (the module bundler has no CSS
 *  loader, so we ship CSS as a <style> tag rather than an import). */
export function injectConformanceStyles(): void {
  if (typeof document === "undefined" || document.getElementById(STYLE_ID)) return;

  const devRules: string[] = [];
  for (let i = 1; i <= DEV_BUCKETS; i++) {
    const { fill, stroke } = devColor(i / DEV_BUCKETS);
    devRules.push(
      `.djs-element.ff-conf-dev-${i} .djs-visual > :nth-child(1){fill:${fill}!important;stroke:${stroke}!important;}`,
    );
  }

  const css = `
.djs-element.ff-conf-ok .djs-visual > :nth-child(1){fill:${CONF_COLORS.ok.fill}!important;stroke:${CONF_COLORS.ok.stroke}!important;}
.djs-element.ff-conf-unmatched .djs-visual > :nth-child(1){fill:${CONF_COLORS.unmatched.fill}!important;stroke:${CONF_COLORS.unmatched.stroke}!important;stroke-dasharray:4 3!important;}
${devRules.join("\n")}
.ff-conf-badge{font:600 10px/1.35 ui-sans-serif,system-ui,sans-serif;background:${CONF_COLORS.badge};color:#fff;padding:1px 5px;border-radius:6px;white-space:nowrap;pointer-events:none;box-shadow:0 1px 2px rgba(0,0,0,.25);}
.ff-conf-badge-warn{background:${CONF_COLORS.badgeWarn};}
${themeCss([...DEV_CLASSES, ...STATE_CLASSES])}
`;
  const style = document.createElement("style");
  style.id = STYLE_ID;
  style.textContent = css;
  document.head.appendChild(style);
}
