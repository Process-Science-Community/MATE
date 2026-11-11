"use client";

/**
 * Viz-registry adapters: render a typed dataset's raw artifact payload
 * (`DatasetEnvelope.artifact`) through the matching artifact viewer. Host-only
 * (not a runtime external) – module panels use the viewers directly.
 */

import { useEffect, useState } from "react";

import type {
  BpmnPayload,
  DfgPayload,
  PetriNetPayload,
  ProcessTreePayload,
} from "../../../../../packages/module-sdk-ts/src/artifacts.generated";
import type { VizComponentProps } from "@/lib/visualizations/types";
import { VizEmpty } from "@/components/visualizations/viz-shell";

import { BpmnView, type BpmnEngine } from "./bpmn";
import { DfgView } from "./dfg";
import { PetriNetView } from "./petri-net";
import { ProcessTreeView } from "./process-tree";

/** The viz card already frames its body – fill it edge-to-edge. */
const CARD_FILL = "h-full w-full overflow-hidden";

function payloadOf<T>(dataset: VizComponentProps["dataset"]): T | null {
  return (dataset.artifact?.payload as T | undefined) ?? null;
}

export function PetriNetArtifactViz({ dataset }: VizComponentProps) {
  const net = payloadOf<PetriNetPayload>(dataset);
  if (!net?.places?.length) return <VizEmpty message={dataset.meta?.note ?? "No Petri net."} />;
  return <PetriNetView net={net} className={CARD_FILL} />;
}

export function ProcessTreeArtifactViz({ dataset }: VizComponentProps) {
  const tree = payloadOf<ProcessTreePayload>(dataset);
  if (!tree?.root) return <VizEmpty message={dataset.meta?.note ?? "No process tree."} />;
  return <ProcessTreeView tree={tree} className={CARD_FILL} />;
}

export function DfgArtifactViz({ dataset }: VizComponentProps) {
  const dfg = payloadOf<DfgPayload>(dataset);
  if (!dfg?.activities) return <VizEmpty message={dataset.meta?.note ?? "No DFG."} />;
  return <DfgView dfg={dfg} />;
}

// Loaded on first use – bpmn-js is large and only BPMN cards need it.
let enginePromise: Promise<BpmnEngine> | undefined;
function loadBpmnEngine(): Promise<BpmnEngine> {
  if (!enginePromise) {
    const p: Promise<BpmnEngine> = Promise.all([
      import("bpmn-js/lib/NavigatedViewer"),
      import("bpmn-auto-layout"),
      import("diagram-js-minimap"),
    ]).then(([viewer, autoLayout, minimap]) => ({
      Viewer: viewer.default,
      layoutProcess: autoLayout.layoutProcess,
      minimapModule: minimap.default,
    }));
    // A failed chunk load must not poison the cache – the next card retries.
    p.catch(() => {
      if (enginePromise === p) enginePromise = undefined;
    });
    enginePromise = p;
  }
  return enginePromise;
}

export function BpmnArtifactViz({ dataset }: VizComponentProps) {
  const bpmn = payloadOf<BpmnPayload>(dataset);
  const [engine, setEngine] = useState<BpmnEngine | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let alive = true;
    loadBpmnEngine().then(
      (e) => alive && setEngine(e),
      () => alive && setFailed(true),
    );
    return () => {
      alive = false;
    };
  }, []);

  if (!bpmn?.xml) return <VizEmpty message={dataset.meta?.note ?? "No BPMN model."} />;
  if (failed) return <VizEmpty message="Couldn't load the BPMN viewer." />;
  if (!engine) return <VizEmpty message="Loading the BPMN viewer…" />;
  return <BpmnView engine={engine} xml={bpmn.xml} />;
}
