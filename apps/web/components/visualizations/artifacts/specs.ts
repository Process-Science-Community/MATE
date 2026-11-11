/** Catalog group "Process models & graphs": artifact viewers. Offered only for
 * datasets that declare a matching `artifact_type` (`acceptsArtifactTypes`). */

import type { VizSpec } from "@/lib/visualizations/types";

import {
  BpmnArtifactViz,
  DfgArtifactViz,
  PetriNetArtifactViz,
  ProcessTreeArtifactViz,
} from "./artifact-vizzes";

export const artifactSpecs: VizSpec[] = [
  {
    id: "dfg-view",
    title: "Directly-follows graph",
    icon: "Workflow",
    group: "graph",
    description: "Viewer for a dfg artifact: activities and their hand-overs.",
    accepts: ["graph"],
    acceptsArtifactTypes: ["dfg"],
    fields: [],
    defaults: { w: 4, h: 12, minW: 3, minH: 8 },
    Component: DfgArtifactViz,
  },
  {
    id: "petri-net-view",
    // Not "Petri net": that title belongs to the shape-based `petri-net` viz.
    title: "Petri net model",
    icon: "Workflow",
    group: "graph",
    description: "Viewer for a petri_net artifact, with its markings.",
    accepts: ["graph"],
    acceptsArtifactTypes: ["petri_net"],
    fields: [],
    defaults: { w: 6, h: 12, minW: 3, minH: 8 },
    Component: PetriNetArtifactViz,
  },
  {
    id: "process-tree-view",
    title: "Process tree model",
    icon: "ListTree",
    group: "tree",
    description: "Viewer for a process_tree artifact.",
    accepts: ["tree"],
    acceptsArtifactTypes: ["process_tree"],
    fields: [],
    defaults: { w: 6, h: 12, minW: 3, minH: 8 },
    Component: ProcessTreeArtifactViz,
  },
  {
    id: "bpmn-view",
    title: "BPMN model",
    icon: "Workflow",
    group: "graph",
    description: "Viewer for a bpmn artifact (BPMN 2.0 XML).",
    accepts: ["blob"],
    acceptsArtifactTypes: ["bpmn"],
    fields: [],
    defaults: { w: 6, h: 12, minW: 3, minH: 8 },
    Component: BpmnArtifactViz,
  },
];
