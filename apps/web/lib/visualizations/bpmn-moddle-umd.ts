// shortcut: `next dev --turbo` mis-bundles bpmn-moddle's ESM build (moddle types
// resolve to undefined, so every host-side importXML/layoutProcess fails with
// "failed to parse document as <bpmn:Definitions>"). next.config.ts points Turbopack's
// `bpmn-moddle` at this shim over the self-contained UMD build; webpack (prod) is
// untouched. Drop it (and the alias) once Turbopack bundles bpmn-moddle correctly.
// @ts-expect-error - resolved by the Turbopack alias in next.config.ts; the UMD has no types.
import * as umd from "bpmn-moddle-umd";

// The package is `"type": "module"`, so the UMD finds no `module` and sets a global
// instead. `umd` must be referenced: the package's `sideEffects: false` drops a bare
// import. The factory returns the instance, so `new BpmnModdle(packages, options)` works.
export const BpmnModdle =
  (umd as { default?: unknown }).default ?? (globalThis as { BpmnModdle?: unknown }).BpmnModdle;
