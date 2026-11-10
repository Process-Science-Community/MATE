"use client";

import { createContext, useContext, useEffect, type ReactNode } from "react";
import { Handle, Position, useUpdateNodeInternals, type NodeProps, type NodeTypes } from "@xyflow/react";
import { Box, Boxes, Database, ExternalLink, Pin, Save } from "lucide-react";

import { cn } from "@/lib/cn";
import { StatusBadge } from "@/components/status-badge";
import type { FlowModuleData, FlowOutputData, FlowRun, FlowSourceData } from "@/lib/flow-queries";

import {
  ANY_TYPE,
  LOG_HANDLE,
  OUTPUT_HANDLE,
  typeColor,
  typeLabel,
  type FlowNode,
  type Lookup,
} from "./flow-graph";

/** What the node renderers read besides their own data (nodeTypes must stay static). */
export interface FlowCanvasState {
  lookup: Lookup;
  run: FlowRun | null;
  /** node id → problems from the last save/validate/run. */
  issues: Map<string, string[]>;
  /** `${node}:${handle}` of every bound input port. */
  bound: Set<string>;
  artifactNames: Map<string, string>;
  /** output node id → the artifact ref it saved in the shown run. */
  outputRefs: Map<string, string>;
  openArtifact: (ref: string) => void;
}

export const FlowCanvasContext = createContext<FlowCanvasState | null>(null);
const useFlowCanvas = () => useContext(FlowCanvasContext)!;

function NodeFrame({
  id,
  selected,
  icon: Icon,
  title,
  subtitle,
  children,
}: {
  id: string;
  selected: boolean;
  icon: typeof Box;
  title: string;
  subtitle?: ReactNode;
  children?: ReactNode;
}) {
  const ctx = useFlowCanvas();
  const run = ctx.run?.nodes[id];
  const issues = ctx.issues.get(id);
  return (
    <div
      className={cn(
        "w-60 rounded-lg border bg-card text-card-foreground shadow-sm",
        selected && "ring-2 ring-primary",
        issues && "border-destructive",
      )}
    >
      <div className="flex items-center gap-2 border-b px-3 py-2">
        <Icon className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
        <div className="min-w-0 flex-1">
          <p className="truncate text-xs font-semibold">{title}</p>
          {subtitle && <p className="truncate text-[10px] text-muted-foreground">{subtitle}</p>}
        </div>
        {run && <StatusBadge status={run.status} className="h-5 px-1.5 text-[10px]" />}
      </div>
      <div className="py-1.5">{children}</div>
      {(issues || run?.error) && (
        <p className="line-clamp-3 border-t px-3 py-1.5 text-[10px] text-destructive">
          {issues?.join(" · ") ?? run?.error}
        </p>
      )}
    </div>
  );
}

/** One port: a typed handle on the node's edge plus its label. */
function PortRow({
  side,
  id,
  label,
  type,
  required,
  hint,
  onOpen,
}: {
  side: "source" | "target";
  id: string;
  label: string;
  type: string | null;
  required?: boolean;
  hint?: string;
  onOpen?: () => void;
}) {
  const color = typeColor(type);
  return (
    <div
      className={cn(
        "relative flex h-6 items-center gap-1.5 px-3 text-[11px]",
        side === "source" && "flex-row-reverse text-right",
      )}
    >
      <Handle
        type={side}
        position={side === "target" ? Position.Left : Position.Right}
        id={id}
        title={`${label}: ${typeLabel(type)}`}
        className="!h-2.5 !w-2.5 !border-2 !border-card"
        style={{ background: color }}
      />
      <span className="truncate font-medium">
        {label}
        {required && (
          <span className="text-destructive" aria-label="required">
            *
          </span>
        )}
      </span>
      <span className="truncate text-[10px] text-muted-foreground">{hint ?? typeLabel(type)}</span>
      {onOpen && (
        <button
          type="button"
          onClick={onOpen}
          className="nodrag nopan rounded p-0.5 text-muted-foreground hover:bg-muted hover:text-foreground"
          aria-label={`Open ${label}`}
          title="Open artifact"
        >
          <ExternalLink className="h-3 w-3" />
        </button>
      )}
    </div>
  );
}

function SourceNode({ id, data, selected }: NodeProps<FlowNode>) {
  const ctx = useFlowCanvas();
  const d = data as unknown as FlowSourceData;
  const updateNodeInternals = useUpdateNodeInternals();
  const ref = d.kind === "log" ? null : d.ref;
  // The handle id is the ref: re-pointing keeps the node's size, so React Flow would
  // keep the old handle and lose the re-pointed edge.
  useEffect(() => updateNodeInternals(id), [id, ref, updateNodeInternals]);
  if (d.kind === "log") {
    return (
      <NodeFrame id={id} selected={selected} icon={Database} title="Event log" subtitle="Every module reads it">
        <PortRow side="source" id={LOG_HANDLE} label="log" type={ctx.lookup.logType} />
      </NodeFrame>
    );
  }
  const type = d.ref ? (ctx.lookup.artifactTypes.get(d.ref) ?? null) : null;
  return (
    <NodeFrame
      id={id}
      selected={selected}
      icon={Box}
      title={(d.ref && ctx.artifactNames.get(d.ref)) || "Artifact"}
      subtitle={d.ref ? "From your workspace" : "Pick an artifact in the inspector"}
    >
      {d.ref && <PortRow side="source" id={d.ref} label="artifact" type={type} />}
    </NodeFrame>
  );
}

function ModuleNode({ id, data, selected }: NodeProps<FlowNode>) {
  const ctx = useFlowCanvas();
  const d = data as unknown as FlowModuleData;
  const entry = ctx.lookup.modules.get(d.module_id);
  const refs = ctx.run?.nodes[id]?.refs ?? [];
  return (
    <NodeFrame
      id={id}
      selected={selected}
      icon={Boxes}
      title={entry?.name ?? d.module_id}
      subtitle={entry ? undefined : "Not installed"}
    >
      {entry?.inputs.map((p) => (
        <PortRow
          key={`in:${p.id}`}
          side="target"
          id={p.id}
          label={p.id}
          type={p.artifact_type}
          required={p.required}
          // An unconnected port with a default producer reads that module's result.
          hint={p.default_bound && !ctx.bound.has(`${id}:${p.id}`) ? `default: ${p.from}` : undefined}
        />
      ))}
      {entry?.outputs.map((o) => {
        const ref = refs.find((r) => r.dataset_id === o.dataset_id)?.ref;
        return (
          <PortRow
            key={`out:${o.dataset_id}`}
            side="source"
            id={o.dataset_id}
            label={o.title || o.dataset_id}
            type={o.artifact_type}
            onOpen={ref ? () => ctx.openArtifact(ref) : undefined}
          />
        );
      })}
    </NodeFrame>
  );
}

function OutputNode({ id, data, selected }: NodeProps<FlowNode>) {
  const ctx = useFlowCanvas();
  const d = data as unknown as FlowOutputData;
  const ref = ctx.outputRefs.get(id);
  return (
    <NodeFrame
      id={id}
      selected={selected}
      icon={Save}
      title={d.name || "Output"}
      subtitle={
        <span className="inline-flex items-center gap-1">
          Saved to the workspace{d.pin && <Pin className="h-2.5 w-2.5" aria-label="pinned" />}
        </span>
      }
    >
      <PortRow
        side="target"
        id={OUTPUT_HANDLE}
        label="result"
        type={ANY_TYPE}
        onOpen={ref ? () => ctx.openArtifact(ref) : undefined}
      />
    </NodeFrame>
  );
}

export const FLOW_NODE_TYPES: NodeTypes = {
  source: SourceNode,
  module: ModuleNode,
  output: OutputNode,
};
