"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  applyEdgeChanges,
  applyNodeChanges,
  useReactFlow,
  type Connection,
  type EdgeChange,
  type NodeChange,
  type OnConnectEnd,
} from "@xyflow/react";
import {
  Check,
  CheckCircle2,
  LayoutGrid,
  Loader2,
  Play,
  Redo2,
  Square,
  Undo2,
  Workflow,
  X,
} from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState } from "@/components/empty-state";
import { StatusBadge } from "@/components/status-badge";
import { ArtifactViewer } from "@/components/artifacts/artifact-viewer";
import { CanvasShell } from "@/components/visualizations/canvases/shared/canvas-shell";
import { elkLayout } from "@/components/visualizations/artifacts/layout";
import { toastError } from "@/lib/toast";
import { useEventLogs } from "@/lib/queries";
import { useArtifacts } from "@/lib/artifact-queries";
import { useItemHistory } from "@/lib/dashboards/use-item-history";
import {
  flowIssues,
  isRunInFlight,
  useCancelFlowRun,
  useFlow,
  useFlowPalette,
  useFlowRun,
  useFlowRuns,
  useRunFlow,
  useUpdateFlow,
  useValidateFlow,
  type FlowConverter,
  type FlowIssue,
} from "@/lib/flow-queries";

import {
  connectionProblem,
  converterProblem,
  findConverter,
  fromFlowGraph,
  insertConverter,
  newId,
  sourceType,
  targetType,
  toFlowGraph,
  typeColor,
  typeLabel,
  withEdge,
  type Candidate,
  type EditorGraph,
  type FlowNode,
  type Lookup,
} from "./flow-graph";
import { FLOW_NODE_TYPES, FlowCanvasContext, type FlowCanvasState } from "./flow-nodes";
import { FlowPalette, type FlowAddRequest } from "./flow-palette";
import { FlowInspector, type InspectorActions } from "./flow-inspector";

/** Autosave debounce: a burst of edits (drag, typing) coalesces into one PUT. */
const AUTOSAVE_MS = 1000;
/** Half a node, so a dropped node centres on the cursor. */
const DROP_OFFSET = { x: 120, y: 40 };
/** A node's width (w-60) plus a gap, and a header's height: closer is overlapping. */
const NODE_GAP = { x: 280, y: 80 };

interface Mismatch {
  candidate: Candidate;
  problem: string;
  converter: FlowConverter | null;
}

type RfApi = ReturnType<typeof useReactFlow>;

/** Exposes the canvas' React Flow instance (CanvasShell owns the provider). */
function RfBridge({ apiRef }: { apiRef: React.MutableRefObject<RfApi | null> }) {
  const rf = useReactFlow();
  useEffect(() => {
    apiRef.current = rf;
  }, [rf, apiRef]);
  return null;
}

type LooseConnection = Pick<Connection, "source" | "target"> & {
  sourceHandle?: string | null;
  targetHandle?: string | null;
};
const asCandidate = (c: LooseConnection): Candidate | null =>
  c.sourceHandle && c.targetHandle
    ? { source: c.source, sourceHandle: c.sourceHandle, target: c.target, targetHandle: c.targetHandle }
    : null;

/** Focus is on the canvas (a node, an edge, the pane) – not the inspector or a field. */
const focusInCanvas = () => !!document.activeElement?.closest(".react-flow");

const isTyping = (el: HTMLElement) =>
  el.isContentEditable ||
  ["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName) ||
  !!el.closest('[data-radix-popper-content-wrapper], [role="dialog"], [role="listbox"], [role="menu"]');

/**
 * The flow editor: palette | canvas | inspector. Local state is the source of truth
 * after hydration and autosaves (PUT replaces the graph); every edit goes through
 * the undo history.
 */
export function FlowEditor({ flowId }: { flowId: string }) {
  const { data: flow, isLoading, isError } = useFlow(flowId);
  const { data: palette, isLoading: paletteLoading } = useFlowPalette(flow?.log_model);
  const { data: artifacts } = useArtifacts();
  const { data: logs } = useEventLogs({ status: "ready" });
  const { data: runs } = useFlowRuns(flowId);
  const update = useUpdateFlow();
  const validate = useValidateFlow(flowId);
  const runFlow = useRunFlow(flowId);
  const cancelRun = useCancelFlowRun(flowId);

  const [name, setName] = useState("");
  const [graph, setGraphState] = useState<EditorGraph>({ nodes: [], edges: [] });
  const graphRef = useRef(graph);
  const setGraph = useCallback((g: EditorGraph) => {
    graphRef.current = g;
    setGraphState(g);
  }, []);
  const history = useItemHistory(graph, setGraph);
  const { commit, undo, redo } = history;
  // The last Validate/Run's problems, kept until the next one; a rejected autosave's
  // own problems show over them until a save goes through.
  const [checkIssues, setCheckIssues] = useState<FlowIssue[]>([]);
  const [saveIssues, setSaveIssues] = useState<FlowIssue[] | null>(null);
  const [logChoice, setLogChoice] = useState<string | null>(null);
  const [runChoice, setRunChoice] = useState<string | null>(null);
  const [openRef, setOpenRef] = useState<string | null>(null);
  const [mismatch, setMismatch] = useState<Mismatch | null>(null);
  const [fitKey, setFitKey] = useState(0);
  const [ghost, setGhost] = useState<{ label: string; x: number; y: number } | null>(null);
  const rfRef = useRef<RfApi | null>(null);
  const canvasRef = useRef<HTMLDivElement>(null);
  // The pre-drag graph while a node drag streams positions (see onNodesChange).
  const dragOrigin = useRef<EditorGraph | null>(null);

  // ── Lookups ──────────────────────────────────────────────────────────────
  const lookup = useMemo<Lookup>(
    () => ({
      modules: new Map((palette?.modules ?? []).map((m) => [m.module_id, m])),
      logType: palette?.log_artifact_type ?? (flow?.log_model === "object_centric" ? "ocel" : "event_log"),
      artifactTypes: new Map((artifacts ?? []).map((a) => [a.ref, a.artifact_type])),
    }),
    [palette, artifacts, flow?.log_model],
  );
  const lookupRef = useRef(lookup);
  lookupRef.current = lookup;

  const readyLogs = useMemo(
    () => (logs ?? []).filter((l) => l.log_model === flow?.log_model),
    [logs, flow?.log_model],
  );
  const logId = logChoice ?? runs?.[0]?.log_id ?? readyLogs[0]?.id ?? null;
  const shownRunId = runChoice ?? runs?.[0]?.id ?? null;
  const { data: run } = useFlowRun(flowId, shownRunId);
  const running = isRunInFlight(run?.status);

  // ── Hydrate + autosave (the dashboard editor's scheme) ───────────────────
  const [saveState, setSaveState] = useState<"idle" | "saving" | "saved" | "invalid" | "error">("idle");
  const hydrated = useRef(false);
  const savedKey = useRef("");
  const saving = useRef<Promise<void> | null>(null);
  const body = () => ({
    name: name.trim() || "Untitled flow",
    description: flow?.description ?? null,
    graph: toFlowGraph(graphRef.current),
  });
  const bodyRef = useRef(body);
  bodyRef.current = body;
  const updateRef = useRef(update.mutateAsync);
  updateRef.current = update.mutateAsync;

  useEffect(() => {
    if (!flow || hydrated.current) return;
    hydrated.current = true;
    setName(flow.name);
    setGraph(fromFlowGraph(flow.graph));
    savedKey.current = JSON.stringify({
      name: flow.name,
      description: flow.description ?? null,
      graph: toFlowGraph(fromFlowGraph(flow.graph)),
    });
  }, [flow, setGraph]);

  // One save at a time; edits landing meanwhile are saved right after it.
  const runSave = useRef<() => Promise<void>>(async () => {});
  runSave.current = async () => {
    if (!hydrated.current) return;
    if (saving.current) return saving.current;
    const b = bodyRef.current();
    const key = JSON.stringify(b);
    if (key === savedKey.current) return;
    setSaveState("saving");
    let ok = false;
    saving.current = (async () => {
      try {
        await updateRef.current({ id: flowId, ...b });
        savedKey.current = key;
        ok = true;
        setSaveState("saved");
        setSaveIssues(null);
      } catch (e) {
        const found = flowIssues(e);
        if (found) {
          // Kept unsaved: the server holds the last valid graph until these are fixed.
          setSaveIssues(found);
          setSaveState("invalid");
        } else {
          setSaveState("error");
          toast.error("Could not save the flow");
        }
      }
    })();
    await saving.current;
    saving.current = null;
    if (ok && JSON.stringify(bodyRef.current()) !== savedKey.current) await runSave.current();
  };

  const persistKey = useMemo(
    () => JSON.stringify({ name: name.trim() || "Untitled flow", description: flow?.description ?? null, graph: toFlowGraph(graph) }),
    [name, graph, flow?.description],
  );
  useEffect(() => {
    if (!hydrated.current || persistKey === savedKey.current) return;
    const t = setTimeout(() => void runSave.current(), AUTOSAVE_MS);
    return () => clearTimeout(t);
  }, [persistKey]);
  // Flush on unmount (navigating away); React Query does not cancel the mutation.
  useEffect(() => () => void runSave.current(), []);

  /** Wait for any save in flight, then save what is pending. True when the server
   *  now holds exactly the editor's graph. */
  const flush = async () => {
    await saving.current;
    await runSave.current();
    return JSON.stringify(bodyRef.current()) === savedKey.current;
  };

  // ── Graph edits ──────────────────────────────────────────────────────────
  const selectOnly = useCallback(
    (nodeId: string | null, g: EditorGraph = graphRef.current): EditorGraph => ({
      nodes: g.nodes.map((n) => ({ ...n, selected: n.id === nodeId })),
      edges: g.edges.map((e) => ({ ...e, selected: false })),
    }),
    [],
  );

  const onNodesChange = useCallback(
    (changes: NodeChange[]) => {
      // Delete only acts while focus is on the canvas, never from the inspector.
      const kept = changes.filter((c) => c.type !== "remove" || focusInCanvas());
      if (!kept.length) return;
      const cur = graphRef.current;
      const next = { nodes: applyNodeChanges(kept as NodeChange<FlowNode>[], cur.nodes), edges: cur.edges };
      const removed = new Set(kept.flatMap((c) => (c.type === "remove" ? [c.id] : [])));
      if (removed.size) {
        next.edges = cur.edges.filter((e) => !removed.has(e.source) && !removed.has(e.target));
        commit(next, "remove");
        return;
      }
      const moves = kept.filter((c) => c.type === "position");
      if (moves.some((c) => c.type === "position" && c.dragging)) {
        dragOrigin.current ??= cur;
        setGraph(next);
      } else if (moves.length && dragOrigin.current) {
        // Drag end: one undo step back to where the drag started.
        commit(next, "move", dragOrigin.current);
        dragOrigin.current = null;
      } else if (moves.length) {
        // Arrow keys: the history coalesces a burst of same-label nudges into one step.
        commit(next, "nudge");
      } else {
        setGraph(next);
      }
    },
    [commit, setGraph],
  );

  const onEdgesChange = useCallback(
    (changes: EdgeChange[]) => {
      const kept = changes.filter((c) => c.type !== "remove" || focusInCanvas());
      if (!kept.length) return;
      const cur = graphRef.current;
      const next = { nodes: cur.nodes, edges: applyEdgeChanges(kept, cur.edges) };
      if (kept.some((c) => c.type === "remove")) commit(next, "remove");
      else setGraph(next);
    },
    [commit, setGraph],
  );

  const isValidConnection = useCallback((c: LooseConnection) => {
    const cand = asCandidate(c);
    return !!cand && !connectionProblem(graphRef.current, cand, lookupRef.current);
  }, []);

  const onConnect = useCallback(
    (c: Connection) => {
      const cand = asCandidate(c);
      if (!cand || connectionProblem(graphRef.current, cand, lookupRef.current)) return;
      setMismatch(null);
      commit({ ...graphRef.current, edges: withEdge(graphRef.current.edges, cand) }, "connect");
    },
    [commit],
  );

  // A drop on an incompatible port: say why, and offer the converter if there is one.
  const onConnectEnd = useCallback<OnConnectEnd>(
    (_e, state) => {
      if (state.isValid !== false || !state.fromHandle || !state.toHandle) return;
      const [from, to] =
        state.fromHandle.type === "source" ? [state.fromHandle, state.toHandle] : [state.toHandle, state.fromHandle];
      if (from.type !== "source" || to.type !== "target") return;
      const cand = asCandidate({ source: from.nodeId, sourceHandle: from.id, target: to.nodeId, targetHandle: to.id });
      if (!cand) return;
      const g = graphRef.current;
      const problem = connectionProblem(g, cand, lookupRef.current);
      if (!problem) return;
      const src = sourceType(g.nodes.find((n) => n.id === cand.source), cand.sourceHandle, lookupRef.current);
      const tgt = targetType(g.nodes.find((n) => n.id === cand.target), cand.targetHandle, lookupRef.current);
      const converter = src && tgt ? (findConverter(palette?.converters ?? [], src, tgt) ?? null) : null;
      setMismatch({ candidate: cand, problem, converter });
    },
    [palette],
  );

  const addNode = useCallback(
    (req: FlowAddRequest, at: { x: number; y: number } | null) => {
      const rect = canvasRef.current?.getBoundingClientRect();
      const pos =
        at ??
        rfRef.current?.screenToFlowPosition({
          x: (rect?.left ?? 0) + (rect?.width ?? 0) / 2,
          y: (rect?.top ?? 0) + (rect?.height ?? 0) / 2,
        }) ?? { x: 0, y: 0 };
      const position = { x: pos.x - DROP_OFFSET.x, y: pos.y - DROP_OFFSET.y };
      // A click/keyboard add lands at the view centre: step right past any node there
      // (the log source sits there on a new flow), so adds line up left to right.
      while (
        !at &&
        graphRef.current.nodes.some(
          (n) => Math.abs(n.position.x - position.x) < NODE_GAP.x && Math.abs(n.position.y - position.y) < NODE_GAP.y,
        )
      ) {
        position.x += NODE_GAP.x;
      }
      const node: FlowNode =
        req.type === "module"
          ? { id: newId("module"), type: "module", position, data: { module_id: req.module_id, params: {} } }
          : req.type === "output"
            ? { id: newId("output"), type: "output", position, data: { name: "", pin: true } }
            : { id: newId("source"), type: "source", position, data: { kind: "artifact", ref: "" } };
      const g = selectOnly(null);
      // Selected, so the inspector opens on it (an artifact source needs its pick).
      commit({ nodes: [...g.nodes, { ...node, selected: true }], edges: g.edges }, "add");
      // Stepped right, it may sit outside the view: bring it in (a drop is already visible).
      if (!at) setFitKey((k) => k + 1);
    },
    [commit, selectOnly],
  );

  // Palette → canvas: the dashboard palette's pointer gesture. Released over the
  // canvas = drop there; released without moving = add at the view's centre.
  const startAdd = useCallback(
    (req: FlowAddRequest, e: React.PointerEvent) => {
      const label =
        req.type === "module"
          ? (lookupRef.current.modules.get(req.module_id)?.name ?? req.module_id)
          : req.type === "output"
            ? "Save to workspace"
            : "Artifact";
      const sx = e.clientX;
      const sy = e.clientY;
      let moved = false;
      const move = (ev: PointerEvent) => {
        if (Math.abs(ev.clientX - sx) > 4 || Math.abs(ev.clientY - sy) > 4) moved = true;
        if (moved) setGhost({ label, x: ev.clientX, y: ev.clientY });
      };
      const finish = (ev: PointerEvent) => {
        cleanup();
        const r = canvasRef.current?.getBoundingClientRect();
        const inside =
          r && ev.clientX >= r.left && ev.clientX <= r.right && ev.clientY >= r.top && ev.clientY <= r.bottom;
        if (moved && inside && rfRef.current) {
          addNode(req, rfRef.current.screenToFlowPosition({ x: ev.clientX, y: ev.clientY }));
        } else if (!moved) addNode(req, null);
      };
      const cleanup = () => {
        window.removeEventListener("pointermove", move);
        window.removeEventListener("pointerup", finish);
        window.removeEventListener("pointercancel", cleanup);
        setGhost(null);
      };
      window.addEventListener("pointermove", move);
      window.addEventListener("pointerup", finish);
      window.addEventListener("pointercancel", cleanup);
    },
    [addNode],
  );

  const actions: InspectorActions = {
    setData: (id, data, label) => {
      const g = graphRef.current;
      const nodes = g.nodes.map((n) => (n.id === id ? { ...n, data } : n));
      let edges = g.edges;
      const ref = (data as { ref?: string }).ref;
      if (ref !== undefined) {
        // An artifact source's output handle is its ref: re-point its edges, or drop
        // the ones the new artifact's type no longer fits.
        const problems: string[] = [];
        edges = g.edges.flatMap((e) => {
          if (e.source !== id) return [e];
          const c = { source: id, sourceHandle: ref, target: e.target, targetHandle: e.targetHandle ?? "" };
          const problem = connectionProblem({ nodes, edges: g.edges }, c, lookupRef.current);
          if (problem) problems.push(problem);
          return problem ? [] : [{ ...e, sourceHandle: ref }];
        });
        if (problems.length) {
          toast.warning(`Removed ${problems.length} connection${problems.length === 1 ? "" : "s"}`, {
            description: [...new Set(problems)].join(". "),
          });
        }
      }
      commit({ nodes, edges }, label);
    },
    bind: (target, targetHandle, source) => {
      const g = graphRef.current;
      const edges = source
        ? withEdge(g.edges, { source: source.id, sourceHandle: source.handle, target, targetHandle })
        : g.edges.filter((e) => !(e.target === target && e.targetHandle === targetHandle));
      commit({ ...g, edges }, `bind:${target}:${targetHandle}`);
    },
    removeNode: (id) => {
      const g = graphRef.current;
      commit(
        { nodes: g.nodes.filter((n) => n.id !== id), edges: g.edges.filter((e) => e.source !== id && e.target !== id) },
        "remove",
      );
    },
    removeEdge: (id) => commit({ ...graphRef.current, edges: graphRef.current.edges.filter((e) => e.id !== id) }, "remove"),
    selectNode: (id) => setGraph(selectOnly(id)),
    openArtifact: setOpenRef,
  };

  const autoLayout = async () => {
    const g = graphRef.current;
    const { nodes } = await elkLayout(g.nodes, g.edges, {
      direction: "RIGHT",
      nodeNodeBetweenLayers: 100,
      nodeSize: (n) => (n.measured?.width ? { width: n.measured.width, height: n.measured.height ?? 100 } : undefined),
      defaultSize: { width: 240, height: 120 },
    });
    const pos = new Map(nodes.map((n) => [n.id, n.position]));
    const cur = graphRef.current;
    commit({ ...cur, nodes: cur.nodes.map((n) => ({ ...n, position: pos.get(n.id) ?? n.position })) }, "layout");
    setFitKey((k) => k + 1);
  };

  // ⌘Z / ⌘⇧Z (and ⌘Y) – never while typing or inside a popover.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!(e.metaKey || e.ctrlKey) || isTyping(e.target as HTMLElement)) return;
      const k = e.key.toLowerCase();
      if (k === "z" && !e.shiftKey) undo();
      else if ((k === "z" && e.shiftKey) || k === "y") redo();
      else return;
      e.preventDefault();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [undo, redo]);

  // ── Run / validate ───────────────────────────────────────────────────────
  /** The count, plus the problems no node or edge marks (only the inspector's flow view lists them). */
  const toastIssues = (title: string, found: FlowIssue[]) =>
    toastError(`${title}: ${found.length} problem${found.length === 1 ? "" : "s"}`, {
      description:
        found
          .filter((it) => !it.node_id && !it.edge_id)
          .map((it) => it.message)
          .join("\n") || "Marked on the canvas.",
    });

  const onValidate = async () => {
    if (!(await flush())) return toast.error("Fix the problems in the inspector first");
    try {
      const found = await validate.mutateAsync(logId);
      setCheckIssues(found);
      if (found.length === 0) toast.success("No problems found");
      else toastIssues("Not valid", found);
    } catch (e) {
      toastError("Could not validate the flow", { description: (e as Error).message });
    }
  };

  const onRun = async () => {
    if (!logId) return;
    if (!(await flush())) return toast.error("Fix the problems in the inspector first");
    try {
      const r = await runFlow.mutateAsync(logId);
      setRunChoice(r.id);
      setCheckIssues([]);
    } catch (e) {
      const found = flowIssues(e);
      if (found) {
        setCheckIssues(found);
        toastIssues("Can't run", found);
      } else toastError("Could not start the run", { description: (e as Error).message });
    }
  };

  // ── Derived canvas state ─────────────────────────────────────────────────
  // A deleted node's or edge's markers go with it; the rest stay.
  const issues = useMemo(() => {
    const ids = new Set([...graph.nodes.map((n) => n.id), ...graph.edges.map((e) => e.id)]);
    return (saveIssues ?? checkIssues).filter(
      (it) => (!it.node_id || ids.has(it.node_id)) && (!it.edge_id || ids.has(it.edge_id)),
    );
  }, [graph, saveIssues, checkIssues]);

  const canvasState = useMemo<FlowCanvasState>(() => {
    const byNode = new Map<string, string[]>();
    for (const it of issues) if (it.node_id) byNode.set(it.node_id, [...(byNode.get(it.node_id) ?? []), it.message]);
    const outputRefs = new Map<string, string>();
    for (const n of graph.nodes) {
      if (n.type !== "output" || !run) continue;
      const e = graph.edges.find((x) => x.target === n.id);
      const ref =
        run.nodes[n.id]?.refs[0]?.ref ??
        (e && run.nodes[e.source]?.refs.find((r) => r.dataset_id === e.sourceHandle)?.ref);
      if (ref) outputRefs.set(n.id, ref);
    }
    return {
      lookup,
      run: run ?? null,
      issues: byNode,
      bound: new Set(graph.edges.map((e) => `${e.target}:${e.targetHandle}`)),
      artifactNames: new Map((artifacts ?? []).map((a) => [a.ref, a.name])),
      outputRefs,
      openArtifact: setOpenRef,
    };
  }, [lookup, run, issues, graph, artifacts]);

  const displayEdges = useMemo(() => {
    const bad = new Set(issues.flatMap((it) => (it.edge_id ? [it.edge_id] : [])));
    return graph.edges.map((e) => {
      const t = sourceType(graph.nodes.find((n) => n.id === e.source), e.sourceHandle, lookup);
      return {
        ...e,
        animated: run?.nodes[e.target]?.status === "running",
        style: { stroke: bad.has(e.id) ? "var(--destructive)" : typeColor(t), strokeWidth: 2 },
      };
    });
  }, [graph, lookup, issues, run]);

  const selectedNodes = graph.nodes.filter((n) => n.selected);
  const selectedEdges = graph.edges.filter((e) => e.selected);

  if (isLoading) {
    return (
      <div className="flex h-full flex-col">
        <div className="flex shrink-0 items-center gap-2 border-b border-border px-4 py-2.5 sm:px-6 lg:px-8">
          <Skeleton className="h-8 w-56" />
          <Skeleton className="ml-auto h-8 w-24" />
        </div>
        <Skeleton className="m-4 flex-1" />
      </div>
    );
  }
  if (isError || !flow) {
    return (
      <EmptyState
        icon={Workflow}
        title="Flow not found"
        description="It may have been deleted."
        primaryAction={
          <Button asChild variant="outline">
            <Link href="/flows">Back to flows</Link>
          </Button>
        }
      />
    );
  }

  return (
    <div className="flex h-full flex-col">
      <div className="flex shrink-0 flex-wrap items-center gap-2 border-b border-border px-4 py-2.5 sm:px-6 lg:px-8">
        <Input
          value={name}
          onChange={(e) => setName(e.target.value)}
          className="h-8 w-56 text-sm font-medium"
          placeholder="Flow name"
          aria-label="Flow name"
        />
        <div className="ml-2 flex items-center gap-1.5">
          <span className="text-xs text-muted-foreground">Log</span>
          {logs && readyLogs.length === 0 ? (
            <span id="flow-no-log" className="text-xs text-muted-foreground">
              No ready {flow.log_model === "object_centric" ? "object-centric" : "case-centric"} log to run on.{" "}
              <Link href="/processes/import" className="text-foreground underline underline-offset-2">
                Import one
              </Link>
            </span>
          ) : (
            <Select value={logId ?? undefined} onValueChange={setLogChoice}>
              <SelectTrigger size="sm" className="w-48 text-xs" aria-label="Event log to run on">
                <SelectValue placeholder="No ready log" />
              </SelectTrigger>
              <SelectContent>
                {readyLogs.map((l) => (
                  <SelectItem key={l.id} value={l.id}>
                    {l.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
        </div>

        <div className="ml-auto flex flex-wrap items-center gap-2">
          <span aria-live="polite" className="flex items-center gap-1.5 text-xs text-muted-foreground">
            {saveState === "saving" ? (
              <>
                <Loader2 className="h-3 w-3 animate-spin" />
                Saving…
              </>
            ) : saveState === "saved" ? (
              <>
                <Check className="h-3 w-3" />
                Saved
              </>
            ) : saveState === "invalid" ? (
              <span className="text-destructive">Not saved: fix the problems</span>
            ) : saveState === "error" ? (
              <span className="text-destructive">Save failed</span>
            ) : null}
          </span>
          <span className="flex items-center gap-1">
            <Button variant="ghost" size="icon" className="h-8 w-8" aria-label="Undo" title="Undo (⌘Z)" disabled={!history.canUndo} onClick={history.undo}>
              <Undo2 className="h-3.5 w-3.5" />
            </Button>
            <Button variant="ghost" size="icon" className="h-8 w-8" aria-label="Redo" title="Redo (⌘⇧Z)" disabled={!history.canRedo} onClick={history.redo}>
              <Redo2 className="h-3.5 w-3.5" />
            </Button>
          </span>
          <Button variant="outline" size="sm" onClick={() => void autoLayout()}>
            <LayoutGrid className="mr-1.5 h-3.5 w-3.5" />
            Auto-layout
          </Button>
          <Button variant="outline" size="sm" disabled={validate.isPending} onClick={() => void onValidate()}>
            <CheckCircle2 className="mr-1.5 h-3.5 w-3.5" />
            Validate
          </Button>
          {run && (
            <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
              Last run <StatusBadge status={run.status} className="h-5 px-1.5 text-[10px]" />
            </span>
          )}
          {running && run ? (
            <Button variant="outline" size="sm" disabled={cancelRun.isPending} onClick={() => cancelRun.mutate(run.id)}>
              <Square className="mr-1.5 h-3.5 w-3.5" />
              Cancel
            </Button>
          ) : null}
          <Button
            size="sm"
            disabled={!logId || running || runFlow.isPending}
            aria-describedby={logId ? undefined : "flow-no-log"}
            onClick={() => void onRun()}
          >
            {runFlow.isPending || running ? (
              <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
            ) : (
              <Play className="mr-1.5 h-3.5 w-3.5" />
            )}
            Run
          </Button>
        </div>
      </div>

      <div className="flex min-h-0 flex-1">
        <FlowPalette
          modules={palette?.modules ?? []}
          loading={paletteLoading}
          onStartAdd={startAdd}
          onAdd={(req) => addNode(req, null)}
        />
        <div ref={canvasRef} className="flow-editor relative min-w-0 flex-1">
          <FlowCanvasContext.Provider value={canvasState}>
            <CanvasShell
              className="h-full w-full"
              nodes={graph.nodes}
              edges={displayEdges}
              nodeTypes={FLOW_NODE_TYPES}
              fitViewKey={fitKey}
              fitMaxZoom={1}
              connectable
              onConnect={onConnect}
              isValidConnection={isValidConnection}
              onConnectEnd={onConnectEnd}
              deleteKeyCode={["Backspace", "Delete"]}
              onNodesChange={onNodesChange}
              onEdgesChange={onEdgesChange}
              overlay={
                <>
                  <RfBridge apiRef={rfRef} />
                  {mismatch && (
                    <div
                      role="status"
                      className="absolute left-1/2 top-3 z-10 flex max-w-[90%] -translate-x-1/2 items-center gap-2 rounded-lg border bg-card px-3 py-2 text-xs shadow-md"
                    >
                      <span>{mismatch.problem}.</span>
                      {mismatch.converter && (
                        <Button
                          size="sm"
                          className="h-7 text-xs"
                          onClick={() => {
                            const g = graphRef.current;
                            const conv = mismatch.converter!;
                            const problem = converterProblem(g, mismatch.candidate, conv, lookupRef.current);
                            if (problem) toast.error(`Can't insert the converter: ${problem}`);
                            else commit(insertConverter(g, mismatch.candidate, conv), "converter");
                            setMismatch(null);
                          }}
                        >
                          Insert converter ({typeLabel(mismatch.converter.from_type)} →{" "}
                          {typeLabel(mismatch.converter.to_type)})
                        </Button>
                      )}
                      <Button
                        variant="ghost"
                        size="icon"
                        className="h-6 w-6"
                        aria-label="Dismiss"
                        onClick={() => setMismatch(null)}
                      >
                        <X className="h-3.5 w-3.5" />
                      </Button>
                    </div>
                  )}
                </>
              }
            />
          </FlowCanvasContext.Provider>
        </div>
        <FlowInspector
          graph={graph}
          selectedNode={selectedNodes.length === 1 ? selectedNodes[0]! : null}
          selectedEdge={selectedNodes.length === 0 && selectedEdges.length === 1 ? selectedEdges[0]! : null}
          lookup={lookup}
          artifacts={artifacts ?? []}
          run={run ?? null}
          issues={issues}
          actions={actions}
        />
      </div>

      {ghost && (
        <div
          aria-hidden
          className="pointer-events-none fixed z-50 -translate-x-1/2 -translate-y-1/2 rounded-md border bg-card px-3 py-1.5 text-xs font-medium shadow-lg"
          style={{ left: ghost.x, top: ghost.y }}
        >
          {ghost.label}
        </div>
      )}

      {openRef && <ArtifactDialog artifactRef={openRef} onClose={() => setOpenRef(null)} />}
    </div>
  );
}

/** A run's artifact, viewed in place; the workspace link opens it there. */
function ArtifactDialog({ artifactRef, onClose }: { artifactRef: string; onClose: () => void }) {
  const { data, isLoading } = useArtifacts();
  const row = data?.find((a) => a.ref === artifactRef);
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-4xl">
        <DialogHeader>
          <DialogTitle>{row?.name ?? "Artifact"}</DialogTitle>
          <DialogDescription className="break-all">
            {row ? `${typeLabel(row.artifact_type)} · ` : ""}
            {artifactRef}
          </DialogDescription>
        </DialogHeader>
        {row ? (
          <ArtifactViewer artifact={row} />
        ) : (
          <p className="text-sm text-muted-foreground">
            {isLoading ? "Loading…" : "Not in your workspace yet – it is listed once the run has settled."}
          </p>
        )}
        <DialogFooter>
          <Button asChild variant="outline">
            <Link href={`/artifacts?ref=${encodeURIComponent(artifactRef)}`}>Open in workspace</Link>
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
