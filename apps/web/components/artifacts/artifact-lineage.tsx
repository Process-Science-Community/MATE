"use client";

import { useMemo } from "react";
import { Handle, Position, type Edge, type Node, type NodeProps } from "@xyflow/react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/cn";
import type { ArtifactDetail } from "@/lib/artifact-queries";
import { CanvasShell } from "@/components/visualizations/canvases/shared/canvas-shell";

interface LineageData extends Record<string, unknown> {
  label: string;
  type: string;
  /** The input is in the workspace index, so clicking opens it. */
  linked: boolean;
  current: boolean;
}

function LineageNode({ data }: NodeProps<Node<LineageData>>) {
  return (
    <div
      className={cn(
        "w-52 rounded-md border bg-card px-3 py-1.5 text-xs shadow-sm",
        data.current && "border-primary ring-1 ring-primary/40",
        data.linked && "cursor-pointer hover:border-primary/60",
      )}
      title={data.linked ? "Open this input" : undefined}
    >
      <Handle type="target" position={Position.Left} className="!opacity-0" />
      <div className="truncate font-medium">{data.label}</div>
      <div className="truncate text-[10px] text-muted-foreground">{data.type || "not in workspace"}</div>
      <Handle type="source" position={Position.Right} className="!opacity-0" />
    </div>
  );
}

const nodeTypes = { lineage: LineageNode };
const ROW_H = 64;

/** Read-only one-level lineage: the inputs the producing run bound → this artifact. */
export function ArtifactLineage({
  artifact,
  onSelect,
}: {
  artifact: ArtifactDetail;
  onSelect: (id: string) => void;
}) {
  const { nodes, edges } = useMemo(() => {
    const inputs = artifact.lineage.map(
      (input, i): Node<LineageData> => ({
        id: input.ref,
        type: "lineage",
        position: { x: 0, y: i * ROW_H },
        data: {
          label: input.name ?? input.ref,
          type: input.artifact_type ?? "",
          linked: input.id !== null,
          current: false,
        },
      }),
    );
    const self: Node<LineageData> = {
      id: artifact.ref,
      type: "lineage",
      position: { x: 300, y: ((inputs.length - 1) * ROW_H) / 2 },
      data: { label: artifact.name, type: artifact.artifact_type, linked: false, current: true },
    };
    const links = artifact.lineage.map(
      (input): Edge => ({
        id: `${input.ref}->${artifact.ref}`,
        source: input.ref,
        target: artifact.ref,
        style: { stroke: "var(--muted-foreground)", opacity: 0.6 },
      }),
    );
    return { nodes: [...inputs, self], edges: links };
  }, [artifact]);

  if (artifact.lineage.length === 0) {
    return (
      <p className="text-xs text-muted-foreground">
        {artifact.source === "upload"
          ? "Imported into the workspace - no inputs."
          : "Computed from the event log alone - no input artifacts."}
      </p>
    );
  }
  return (
    <div className="space-y-2">
      <CanvasShell
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        fitViewKey={artifact.id}
        miniMap={false}
        showGrid={false}
        className="h-44 w-full overflow-hidden rounded-lg border bg-card"
        onNodeClick={(_, node) => {
          const id = artifact.lineage.find((l) => l.ref === node.id)?.id;
          if (id) onSelect(id);
        }}
      />
      {/* The graph is pointer-only; the same inputs as buttons for the keyboard. */}
      <ul aria-label="Inputs" className="flex flex-wrap items-center gap-1.5 text-xs">
        {artifact.lineage.map((input) => (
          <li key={input.ref}>
            {input.id ? (
              <Button variant="outline" size="sm" onClick={() => onSelect(input.id!)}>
                {input.name ?? input.ref}
              </Button>
            ) : (
              <span className="text-muted-foreground">{input.name ?? input.ref} (not in workspace)</span>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}
