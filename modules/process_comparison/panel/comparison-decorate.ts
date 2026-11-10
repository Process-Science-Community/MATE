/**
 * Comparison-overlay decoration for the BPMN canvas.
 *
 * Ported from the conformance module's BPMN decorator (modules can't import each
 * other). Instead of colouring tasks by deviation, it colours them by how they
 * change from baseline (A) to comparison (B): green = added or grew, red = removed
 * or shrank, neutral = unchanged. The same per-activity map drives both diagrams –
 * a baseline-only task only exists on the A diagram (shown red, "gone"), a
 * comparison-only task only on the B diagram (green, "new").
 *
 * View-only: decorates via diagram-js *markers* (CSS classes) and *overlays*
 * (floating HTML), never via `modeling.*`, so it never marks the model dirty.
 */

import { BPMN_BLACK } from "@/components/visualizations/artifacts/bpmn";

import type { DfgDiffNode, DiffStatus } from "./types";

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

export type DeltaKind = "up" | "down" | "same";

export interface ComparisonInfo {
  status: DiffStatus;
  freqA: number;
  freqB: number;
  delta: number;
  kind: DeltaKind;
}

export type ActivityMap = Map<string, ComparisonInfo>;

/** Index the diff's per-activity rows by label, tagging each with its delta kind. */
export function buildActivityMap(activities: DfgDiffNode[]): ActivityMap {
  const map: ActivityMap = new Map();
  for (const a of activities) {
    const delta = a.freq_b - a.freq_a;
    const kind: DeltaKind =
      a.status === "only_b" || (a.status === "shared" && delta > 0)
        ? "up"
        : a.status === "only_a" || (a.status === "shared" && delta < 0)
          ? "down"
          : "same";
    map.set(a.label, { status: a.status, freqA: a.freq_a, freqB: a.freq_b, delta, kind });
  }
  return map;
}

const KIND_CLASSES = ["ff-cmp-up", "ff-cmp-down"];

function isTask(el: BElement): boolean {
  return /Task$/.test(el.type) || el.type === "bpmn:SubProcess";
}
function nameOf(el: BElement | undefined): string | undefined {
  const n = el?.businessObject?.name;
  return n && n.length > 0 ? n : undefined;
}

export interface ComparisonDecorOptions {
  map: ActivityMap;
  heatmap: boolean;
  labels: boolean;
}

function clearDecoration(modeler: BpmnModelerLike): void {
  const registry = modeler.get<ElementRegistry>("elementRegistry");
  const overlays = modeler.get<Overlays>("overlays");
  const canvas = modeler.get<Canvas>("canvas");
  overlays.remove({ type: "ff-cmp" });
  for (const el of registry.getAll()) {
    for (const c of KIND_CLASSES) canvas.removeMarker(el.id, c);
  }
}

/** Re-apply the comparison overlay from scratch. Idempotent. */
export function applyComparisonOverlay(
  modeler: BpmnModelerLike,
  opts: ComparisonDecorOptions,
): void {
  const { map, heatmap, labels } = opts;
  clearDecoration(modeler);

  const registry = modeler.get<ElementRegistry>("elementRegistry");
  const overlays = modeler.get<Overlays>("overlays");
  const canvas = modeler.get<Canvas>("canvas");

  for (const el of registry.getAll()) {
    if (!isTask(el)) continue;
    const name = nameOf(el);
    if (!name) continue;
    const info = map.get(name);
    if (!info) continue;

    if (heatmap && info.kind !== "same") canvas.addMarker(el.id, `ff-cmp-${info.kind}`);

    if (labels) {
      const text =
        info.status === "only_b"
          ? "new"
          : info.status === "only_a"
            ? "gone"
            : info.delta === 0
              ? ""
              : `Δ${info.delta > 0 ? "+" : ""}${info.delta}`;
      if (text) {
        const cls = info.kind === "down" ? "ff-cmp-badge ff-cmp-badge-down" : "ff-cmp-badge";
        overlays.add(el.id, "ff-cmp", {
          position: { top: -10, right: 12 },
          html: `<div class="${cls}" title="${name}: ${info.freqA} → ${info.freqB}">${text}</div>`,
        });
      }
    }
  }
}

/**
 * `BpmnView` themes the diagram via CSS variables (dark mode = app tokens).
 * Decoration markers that paint a *light* fill in both themes (the
 * added/removed tints - their lightness range is tuned to keep dark labels
 * readable) must keep dark labels, so they re-assert bpmn-js' default label
 * colour.
 */
function themeCss(litShapeClasses: string[]): string {
  const litLabels = litShapeClasses
    .map((c) => `.dark .djs-element.${c} .djs-visual text`)
    .join(",");
  return `${litLabels}{fill:${BPMN_BLACK}!important;}`;
}

const STYLE_ID = "ff-comparison-styles";

/** Idempotently inject the overlay stylesheet (the module bundler has no CSS
 *  loader, so we ship CSS as a <style> tag rather than an import). */
export function injectComparisonStyles(): void {
  if (typeof document === "undefined" || document.getElementById(STYLE_ID)) return;

  const css = `
.djs-element.ff-cmp-up .djs-visual > :nth-child(1){fill:hsl(151 55% 92%)!important;stroke:hsl(151 60% 35%)!important;}
.djs-element.ff-cmp-down .djs-visual > :nth-child(1){fill:hsl(0 75% 94%)!important;stroke:hsl(0 70% 45%)!important;}
.ff-cmp-badge{font:600 10px/1.35 ui-sans-serif,system-ui,sans-serif;background:hsl(151 60% 35%);color:#fff;padding:1px 5px;border-radius:6px;white-space:nowrap;pointer-events:none;box-shadow:0 1px 2px rgba(0,0,0,.25);}
.ff-cmp-badge-down{background:hsl(0 70% 45%);}
${themeCss(KIND_CLASSES)}
`;
  const style = document.createElement("style");
  style.id = STYLE_ID;
  style.textContent = css;
  document.head.appendChild(style);
}
