// Generated from mate.sdk.artifacts by packages/module-sdk-ts/scripts/gen_artifact_types.py - do not edit.
// Regenerate: uv run python packages/module-sdk-ts/scripts/gen_artifact_types.py

/** Every registered artifact type: `${id}@${version}`, storage, default viz shape. */
export const ARTIFACT_TYPES = {
  event_log: { version: 1, storage: "log", shape: "table", description: "The case-centric event log itself." },
  ocel: { version: 1, storage: "log", shape: "graph", description: "The object-centric event log itself." },
  table: { version: 1, storage: "parquet", shape: "table", description: "A DataFrame stored as Parquet." },
  dfg: { version: 1, storage: "json", shape: "graph", description: "Directly-follows graph." },
  petri_net: { version: 1, storage: "json", shape: "graph", description: "Petri net." },
  process_tree: { version: 1, storage: "json", shape: "tree", description: "Process tree." },
  bpmn: { version: 1, storage: "json", shape: "blob", description: "BPMN 2.0 model as XML." },
  conformance_result: { version: 1, storage: "json", shape: "table", description: "Fitness, precision and deviations of a log against a model." },
  drift_points: { version: 1, storage: "json", shape: "table", description: "Detected concept drifts." },
} as const;

export type ArtifactTypeId = keyof typeof ARTIFACT_TYPES;

/** `bpmn`: a BPMN 2.0 document as XML text, without diagram interchange. */
export interface BpmnPayload {
  xml: string;
  [key: string]: unknown;
}

export interface ConformanceKpis {
  log_fitness: number | null;
  [key: string]: unknown;
}

/** `conformance_result`: one conformance run (conformance's `serialize_conformance`). */
export interface ConformanceResultPayload {
  technique: string;
  kpis?: ConformanceKpis | null;
  [key: string]: unknown;
}

export interface DfgActivity {
  id: string;
  [key: string]: unknown;
}

export interface DfgEdge {
  source: string;
  target: string;
  frequency: number;
  [key: string]: unknown;
}

/** `dfg`: a directly-follows graph (discovery's `serialize_dfg`). */
export interface DfgPayload {
  activities: DfgActivity[];
  edges: DfgEdge[];
  [key: string]: unknown;
}

export interface DriftPoint {
  type: string;
  start_timestamp: string;
  end_timestamp: string;
  confidence: number;
  [key: string]: unknown;
}

/** `drift_points`: detected concept drifts (cv4cdd's `detections`). */
export interface DriftPointsPayload {
  drifts: DriftPoint[];
  [key: string]: unknown;
}

export interface PetriNetArc {
  source: string;
  target: string;
  [key: string]: unknown;
}

/** `petri_net`: places, transitions and arcs (discovery's `serialize_petri_net`). */
export interface PetriNetPayload {
  places: PetriNetPlace[];
  transitions: PetriNetTransition[];
  arcs: PetriNetArc[];
  [key: string]: unknown;
}

export interface PetriNetPlace {
  id: string;
  [key: string]: unknown;
}

export interface PetriNetTransition {
  id: string;
  label: string;
  [key: string]: unknown;
}

export interface ProcessTreeNode {
  operator?: string | null;
  label?: string | null;
  children?: ProcessTreeNode[];
  [key: string]: unknown;
}

/** `process_tree`: a recursive node tree (discovery's `serialize_process_tree`). */
export interface ProcessTreePayload {
  root: ProcessTreeNode;
  [key: string]: unknown;
}

/** The payload of each json artifact type, by artifact type id. */
export interface ArtifactPayloads {
  dfg: DfgPayload;
  petri_net: PetriNetPayload;
  process_tree: ProcessTreePayload;
  bpmn: BpmnPayload;
  conformance_result: ConformanceResultPayload;
  drift_points: DriftPointsPayload;
}
