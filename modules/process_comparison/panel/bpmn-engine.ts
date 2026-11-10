/**
 * The bpmn-js viewer this panel hands the platform `BpmnView` as its engine.
 * Its own file, imported only by the panel's BPMN view: bpmn-js and the minimap
 * have import side effects, so any widget that shares a module with them would
 * bundle them.
 */

// bpmn-js / bpmn-auto-layout / diagram-js-minimap are bundled straight into
// this panel – they are intentionally NOT in runtime-externals.json. The host
// runs `next dev --turbo`, and Turbopack mis-bundles bpmn-moddle so its parser
// stops recognising the `bpmn:Definitions` root: every importXML/layoutProcess
// then fails with "failed to parse document as <bpmn:Definitions>" on valid
// BPMN. esbuild (this panel's bundler) handles bpmn-moddle correctly. Do NOT
// re-externalise these.
import NavigatedViewer from "bpmn-js/lib/NavigatedViewer";
import { layoutProcess } from "bpmn-auto-layout";
// diagram-js-minimap ships JS only (no d.ts); its CSS is imported by apps/web/app/layout.tsx.
import minimapModule from "diagram-js-minimap";

import type { BpmnEngine } from "@/components/visualizations/artifacts/bpmn";

export const BPMN_ENGINE: BpmnEngine = { Viewer: NavigatedViewer, layoutProcess, minimapModule };
