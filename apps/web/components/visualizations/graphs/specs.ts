/** Catalog group "Process models & graphs": graph- and tree-shape vizzes. */

import type { WidgetPropSchema } from "@/lib/dashboard-queries";
import type { VizSpec } from "@/lib/visualizations/types";

import { TreeViz } from "../tree/tree-viz";
import { TreeDiagramViz } from "../tree/tree-diagram";
import { PetriNetViz } from "./petri-net";
import { ProcessMapViz } from "./process-map";

const DIRECTION: WidgetPropSchema = {
  type: "string",
  title: "Direction",
  enum: ["LR", "TB"],
  enumLabels: ["Left → right", "Top → bottom"],
  default: "LR",
  ui: { widget: "segmented" },
};

const pct = (title: string): WidgetPropSchema => ({
  type: "integer",
  title,
  default: 100,
  minimum: 5,
  maximum: 100,
  step: 5,
  ui: { widget: "slider" },
});

/** A raw `dfg` route response (what discovery's GET /dfg returns). */
const DFG_EXAMPLE = {
  kind: "dfg",
  activities: [
    { id: "register", label: "Register", frequency: 120 },
    { id: "check", label: "Check", frequency: 110 },
    { id: "approve", label: "Approve", frequency: 80 },
    { id: "reject", label: "Reject", frequency: 30 },
  ],
  edges: [
    { id: "e1", source: "register", target: "check", frequency: 110, performance_seconds: 3600 },
    { id: "e2", source: "check", target: "approve", frequency: 80, performance_seconds: 7200 },
    { id: "e3", source: "check", target: "reject", frequency: 30, performance_seconds: 600 },
    { id: "e4", source: "check", target: "check", frequency: 10, performance_seconds: 300 },
  ],
  start_activities: { register: 120 },
  end_activities: { approve: 80, reject: 30 },
};

export const graphSpecs: VizSpec[] = [
  {
    id: "process-map",
    title: "Process map",
    icon: "Workflow",
    group: "graph",
    description: "Directly-follows map: activities as nodes, hand-overs as arrows.",
    needs: "A graph dataset: activities (id, label, frequency) and edges (source, target, frequency, optional performance_seconds), e.g. a dfg route.",
    example: DFG_EXAMPLE,
    accepts: ["graph"],
    fields: [],
    options: {
      properties: {
        metric: {
          type: "string",
          title: "Metric",
          enum: ["frequency", "performance"],
          enumLabels: ["Frequency", "Performance"],
          default: "frequency",
          ui: { widget: "segmented" },
        },
        edge_label: {
          type: "string",
          title: "Edge label",
          enum: ["metric", "frequency", "performance", "off"],
          enumLabels: ["Same as metric", "Frequency", "Performance", "Off"],
          default: "metric",
        },
        edge_width: {
          type: "string",
          title: "Edge width",
          enum: ["linear", "log", "off"],
          enumLabels: ["Linear", "Log", "Fixed"],
          default: "linear",
        },
        node_pct: pct("Activities shown (%)"),
        edge_pct: pct("Connections shown (%)"),
        show_terminals: { type: "boolean", title: "Start / end nodes", default: true },
        hide_self_loops: { type: "boolean", title: "Hide self-loops", default: false },
        direction: DIRECTION,
      },
    },
    presets: [
      {
        id: "dfg-performance",
        title: "Performance map",
        description: "Process map coloured and labelled by mean waiting time.",
        icon: "Timer",
        options: { metric: "performance", edge_label: "metric" },
      },
      {
        id: "relation-graph",
        title: "Network graph",
        description: "Plain node-link graph: log-scaled edges, no start/end nodes.",
        icon: "Share2",
        options: { show_terminals: false, edge_width: "log", edge_label: "off" },
      },
    ],
    defaults: { w: 12, h: 12, minW: 3, minH: 8 },
    Component: ProcessMapViz,
  },
  {
    id: "petri-net",
    title: "Petri net",
    icon: "CircleDot",
    group: "graph",
    description: "Places, transitions and silent steps with initial and final markings.",
    needs: "A graph dataset from a petri_net route: places, transitions (label, is_invisible) and arcs (source, target, weight).",
    example: {
      kind: "petri_net",
      places: [
        { id: "p0", label: "", is_initial: true, is_final: false, tokens: 1 },
        { id: "p1", label: "", is_initial: false, is_final: false, tokens: 0 },
        { id: "p2", label: "", is_initial: false, is_final: true, tokens: 0 },
      ],
      transitions: [
        { id: "t0", label: "Register", is_invisible: false },
        { id: "t1", label: "Approve", is_invisible: false },
        { id: "t2", label: "", is_invisible: true },
      ],
      arcs: [
        { id: "a0", source: "p0", target: "t0", weight: 1 },
        { id: "a1", source: "t0", target: "p1", weight: 1 },
        { id: "a2", source: "p1", target: "t1", weight: 1 },
        { id: "a3", source: "t1", target: "p2", weight: 1 },
        { id: "a4", source: "p1", target: "t2", weight: 1 },
        { id: "a5", source: "t2", target: "p2", weight: 1 },
      ],
    },
    accepts: ["graph"],
    fields: [],
    options: {
      properties: {
        show_weights: { type: "boolean", title: "Arc weights above 1", default: true },
        direction: DIRECTION,
      },
    },
    defaults: { w: 12, h: 12, minW: 3, minH: 6 },
    Component: PetriNetViz,
  },
  {
    id: "tree",
    title: "Tree",
    icon: "ListTree",
    group: "tree",
    description: "Indented list of a hierarchy with values.",
    needs: "A tree dataset: a process_tree route (root with label/children) or a prefix_tree route (nodes with id, parent, label, frequency).",
    example: {
      kind: "prefix_tree",
      nodes: [
        { id: "0", label: null, frequency: 120, parent: null },
        { id: "1", label: "Register", frequency: 120, parent: "0" },
        { id: "2", label: "Check", frequency: 110, parent: "1" },
        { id: "3", label: "Approve", frequency: 80, parent: "2" },
        { id: "4", label: "Reject", frequency: 30, parent: "2" },
      ],
    },
    accepts: ["tree"],
    fields: [],
    defaults: { w: 3, h: 10, minW: 2, minH: 6 },
    Component: TreeViz,
  },
  {
    id: "tree-diagram",
    title: "Tree diagram",
    icon: "GitFork",
    group: "tree",
    description: "Node-link tree: process-tree operators or a prefix tree by frequency.",
    needs: "A tree dataset: a process_tree route (root with operator, label, children) or a prefix_tree route.",
    example: {
      kind: "process_tree",
      root: {
        id: "n0",
        operator: "sequence",
        label: null,
        children: [
          { id: "n1", operator: null, label: "Register", children: [] },
          {
            id: "n2",
            operator: "xor",
            label: null,
            children: [
              { id: "n3", operator: null, label: "Approve", children: [] },
              { id: "n4", operator: null, label: "Reject", children: [] },
            ],
          },
          { id: "n5", operator: null, label: null, children: [] },
        ],
      },
    },
    accepts: ["tree"],
    fields: [],
    options: {
      properties: {
        orientation: {
          type: "string",
          title: "Orientation",
          enum: ["TB", "LR"],
          enumLabels: ["Top → bottom", "Left → right"],
          default: "TB",
          ui: { widget: "segmented" },
        },
        max_depth: { type: "integer", title: "Max depth", description: "0 shows all levels.", default: 0, minimum: 0 },
        fold_tau: { type: "boolean", title: "Hide silent (τ) leaves", default: false },
      },
    },
    defaults: { w: 12, h: 12, minW: 3, minH: 6 },
    Component: TreeDiagramViz,
  },
];
