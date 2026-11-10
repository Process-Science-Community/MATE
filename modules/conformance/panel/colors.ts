/**
 * Conformance colours: the single source of truth for the injected canvas CSS
 * (`conformance-decorate.ts`), the panel legend, the tables and the dashboard
 * widgets, so the legend can never drift from what is actually painted.
 *
 * Plain inline values on purpose - Tailwind only compiles classes it sees in
 * `apps/web`, so utility classes that exist only in module sources silently
 * produce no CSS. No imports, also on purpose: widgets read these, and a widget
 * bundle must not pull in the BPMN viewer.
 */

/** Number of discrete red deviation buckets. Mirrored by the injected CSS. */
export const DEV_BUCKETS = 5;

export const CONF_COLORS = {
  /** Task in the model that the log replays without deviations. */
  ok: { fill: "hsl(151 55% 93%)", stroke: "hsl(151 48% 40%)" },
  /** Model task whose label never occurs in the log (name mismatch / never executed). */
  unmatched: { fill: "hsl(38 92% 92%)", stroke: "hsl(33 92% 45%)" },
  /** Deviation-count badge pill. */
  badge: "hsl(0 72% 42%)",
  /** "not in log" warning badge pill. */
  badgeWarn: "hsl(33 90% 42%)",
  /** Red used by deviation bar charts / severity bars. */
  chart: "hsl(0 72% 51%)",
} as const;

/** Map a 0..1 deviation ratio to a red fill/stroke pair. Exported so the
 *  legend can render exactly the same ramp the canvas paints. */
export function devColor(ratio: number): { fill: string; stroke: string } {
  const r = Math.max(0, Math.min(1, ratio));
  const sat = 70 + r * 25; // 70% → 95%
  const fillL = 95 - r * 30; // 95% → 65% (keep dark labels readable)
  const strokeL = 55 - r * 25; // 55% → 30%
  return { fill: `hsl(0 ${sat}% ${fillL}%)`, stroke: `hsl(0 ${sat}% ${strokeL}%)` };
}
