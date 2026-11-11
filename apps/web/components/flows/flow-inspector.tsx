"use client";

import { ExternalLink, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { SchemaForm } from "@/components/schema-form/schema-form";
import { StatusBadge } from "@/components/status-badge";
import type { ArtifactRow } from "@/lib/artifact-queries";
import type {
  FlowIssue,
  FlowModuleData,
  FlowOutputData,
  FlowRun,
  FlowSourceData,
} from "@/lib/flow-queries";

import {
  ANY_TYPE,
  LOG_HANDLE,
  OUTPUT_HANDLE,
  connectionProblem,
  typeColor,
  typeLabel,
  type EditorGraph,
  type FlowNode,
  type Lookup,
} from "./flow-graph";

const UNBOUND = "__unbound__";

export interface InspectorActions {
  /** Replace a node's data; `label` groups the undo step (typing coalesces). */
  setData: (id: string, data: FlowNode["data"], label: string) => void;
  /** Bind `target`'s port to `source`'s output, or unbind it (`source` null). */
  bind: (target: string, targetHandle: string, source: { id: string; handle: string } | null) => void;
  removeNode: (id: string) => void;
  removeEdge: (id: string) => void;
  selectNode: (id: string) => void;
  openArtifact: (ref: string) => void;
}

/** Right rail: settings of the selected node or edge – the keyboard path to every
 *  canvas gesture (binding a port = drawing an edge) – or the flow's problems. */
export function FlowInspector({
  graph,
  selectedNode,
  selectedEdge,
  lookup,
  artifacts,
  run,
  issues,
  actions,
}: {
  graph: EditorGraph;
  selectedNode: FlowNode | null;
  selectedEdge: EditorGraph["edges"][number] | null;
  lookup: Lookup;
  artifacts: ArtifactRow[];
  run: FlowRun | null;
  issues: FlowIssue[];
  actions: InspectorActions;
}) {
  const nodeLabel = (id: string) => {
    const n = graph.nodes.find((x) => x.id === id);
    if (!n) return id;
    if (n.type === "module") {
      const mid = (n.data as unknown as FlowModuleData).module_id;
      return lookup.modules.get(mid)?.name ?? mid;
    }
    if (n.type === "output") return (n.data as unknown as FlowOutputData).name || "Output";
    const d = n.data as unknown as FlowSourceData;
    return d.kind === "log" ? "Event log" : (artifacts.find((a) => a.ref === d.ref)?.name ?? "Artifact");
  };

  /** Every output in the graph that may feed `target`'s port `port`. */
  const candidates = (target: string, port: string) =>
    graph.nodes.flatMap((n) => {
      const d = n.data as unknown as { kind?: string; ref?: string; module_id: string };
      const handles =
        n.type === "module"
          ? (lookup.modules.get(d.module_id)?.outputs ?? []).map((o) => ({
              handle: o.dataset_id,
              label: o.title || o.dataset_id,
            }))
          : n.type === "source" && d.kind === "log"
            ? [{ handle: LOG_HANDLE, label: "log" }]
            : n.type === "source" && d.ref
              ? [{ handle: d.ref, label: "artifact" }]
              : [];
      return handles
        .filter(
          (h) => !connectionProblem(graph, { source: n.id, sourceHandle: h.handle, target, targetHandle: port }, lookup),
        )
        .map((h) => ({ id: n.id, handle: h.handle, label: `${nodeLabel(n.id)} · ${h.label}` }));
    });

  const binding = (target: string, port: string, type: string, required: boolean, from: string | null) => {
    const edge = graph.edges.find((e) => e.target === target && e.targetHandle === port);
    const options = candidates(target, port);
    return (
      <div key={port} className="space-y-1">
        <Label className="flex items-center gap-1.5 text-xs">
          <span aria-hidden className="h-2 w-2 rounded-full" style={{ background: typeColor(type) }} />
          {port}
          {required && <span className="text-destructive">*</span>}
          <span className="font-normal text-muted-foreground">{typeLabel(type)}</span>
        </Label>
        <Select
          value={edge ? JSON.stringify([edge.source, edge.sourceHandle]) : UNBOUND}
          onValueChange={(v) => {
            if (v === UNBOUND) return actions.bind(target, port, null);
            const [id, handle] = JSON.parse(v) as [string, string];
            actions.bind(target, port, { id, handle });
          }}
        >
          <SelectTrigger size="sm" className="w-full text-xs" aria-label={`Input ${port}`}>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={UNBOUND}>{from ? `Default (${from})` : "Not connected"}</SelectItem>
            {options.map((o) => (
              <SelectItem key={`${o.id}:${o.handle}`} value={JSON.stringify([o.id, o.handle])}>
                {o.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
    );
  };

  const nodeRun = selectedNode ? run?.nodes[selectedNode.id] : undefined;

  return (
    <div className="flex h-full w-72 shrink-0 flex-col border-l border-border bg-muted/20">
      <ScrollArea className="min-h-0 flex-1 [&_[data-slot=scroll-area-viewport]>div]:!block">
        <div className="space-y-5 p-4">
          {selectedNode ? (
            <>
              <div className="space-y-1">
                <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                  {selectedNode.type === "module" ? "Module" : selectedNode.type === "output" ? "Output" : "Source"}
                </p>
                <p className="text-sm font-semibold">{nodeLabel(selectedNode.id)}</p>
                {selectedNode.type === "module" && (
                  <p className="text-xs text-muted-foreground">
                    {lookup.modules.get((selectedNode.data as unknown as FlowModuleData).module_id)?.description ??
                      ""}
                  </p>
                )}
              </div>

              {selectedNode.type === "source" &&
                ((selectedNode.data as unknown as FlowSourceData).kind === "log" ? (
                  <p className="text-xs text-muted-foreground">
                    Stands for the event log you pick next to Run. Every module reads it unless
                    you bind its log port elsewhere.
                  </p>
                ) : (
                  <div className="space-y-1">
                    <Label className="text-xs">Artifact</Label>
                    <Select
                      value={(selectedNode.data as { ref: string }).ref || undefined}
                      onValueChange={(ref) =>
                        actions.setData(selectedNode.id, { kind: "artifact", ref }, `ref:${selectedNode.id}`)
                      }
                    >
                      <SelectTrigger size="sm" className="w-full text-xs" aria-label="Artifact">
                        <SelectValue placeholder="Pick an artifact" />
                      </SelectTrigger>
                      <SelectContent>
                        {artifacts
                          .filter((a) => a.state === "ready")
                          .map((a) => (
                            <SelectItem key={a.id} value={a.ref}>
                              {a.name} · {typeLabel(a.artifact_type)}
                            </SelectItem>
                          ))}
                      </SelectContent>
                    </Select>
                  </div>
                ))}

              {selectedNode.type === "module" &&
                (() => {
                  const d = selectedNode.data as unknown as FlowModuleData;
                  const params = d.params ?? {};
                  const entry = lookup.modules.get(d.module_id);
                  const props = entry?.params.properties ?? {};
                  return (
                    <>
                      <div className="space-y-3">
                        <p className="text-xs font-medium text-muted-foreground">Inputs</p>
                        {entry?.inputs.length ? (
                          entry.inputs.map((p) =>
                            binding(selectedNode.id, p.id, p.artifact_type, p.required, p.default_bound ? p.from : null),
                          )
                        ) : (
                          <p className="text-xs text-muted-foreground">
                            None besides the event log, which every module reads.
                          </p>
                        )}
                      </div>
                      {Object.keys(props).length > 0 && (
                        <div className="space-y-3">
                          <p className="text-xs font-medium text-muted-foreground">Parameters</p>
                          <SchemaForm
                            schema={entry?.params}
                            values={params}
                            density="compact"
                            onChange={(key, value) =>
                              actions.setData(
                                selectedNode.id,
                                { ...d, params: { ...params, [key]: value } },
                                `param:${selectedNode.id}:${key}`,
                              )
                            }
                            fieldExtra={(key) =>
                              key in params && (
                                <button
                                  type="button"
                                  className="text-[11px] text-muted-foreground underline-offset-2 hover:underline"
                                  onClick={() => {
                                    const { [key]: _drop, ...rest } = params;
                                    actions.setData(selectedNode.id, { ...d, params: rest }, `reset:${selectedNode.id}`);
                                  }}
                                >
                                  Reset
                                </button>
                              )
                            }
                          />
                        </div>
                      )}
                    </>
                  );
                })()}

              {selectedNode.type === "output" &&
                (() => {
                  const d = selectedNode.data as unknown as FlowOutputData;
                  return (
                    <div className="space-y-3">
                      {binding(selectedNode.id, OUTPUT_HANDLE, ANY_TYPE, true, null)}
                      <div className="space-y-1">
                        <Label htmlFor="flow-output-name" className="text-xs">
                          Name in the workspace
                        </Label>
                        <Input
                          id="flow-output-name"
                          value={d.name}
                          placeholder="The result's own title"
                          onChange={(e) =>
                            actions.setData(selectedNode.id, { ...d, name: e.target.value }, `name:${selectedNode.id}`)
                          }
                          className="h-8 text-xs"
                        />
                      </div>
                      <div className="flex items-center justify-between gap-2">
                        <Label htmlFor="flow-output-pin" className="text-xs">
                          Pin it (survives clean-up)
                        </Label>
                        <Switch
                          id="flow-output-pin"
                          checked={d.pin}
                          onCheckedChange={(pin) => actions.setData(selectedNode.id, { ...d, pin }, `pin:${selectedNode.id}`)}
                        />
                      </div>
                    </div>
                  );
                })()}

              {nodeRun && (
                <div className="space-y-2">
                  <p className="flex items-center gap-2 text-xs font-medium text-muted-foreground">
                    Last run <StatusBadge status={nodeRun.status} className="h-5 px-1.5 text-[10px]" />
                    {nodeRun.reused && <span className="font-normal">reused, nothing changed</span>}
                  </p>
                  {nodeRun.error && <p className="text-xs text-destructive">{nodeRun.error}</p>}
                  {nodeRun.refs.map((r) => (
                    <Button
                      key={r.ref}
                      variant="outline"
                      size="sm"
                      className="w-full justify-start text-xs"
                      onClick={() => actions.openArtifact(r.ref)}
                    >
                      <ExternalLink className="mr-1.5 h-3.5 w-3.5" />
                      Open {r.dataset_id ?? typeLabel(r.artifact_type)}
                    </Button>
                  ))}
                </div>
              )}

              {selectedNode.deletable !== false && (
                <Button
                  variant="outline"
                  size="sm"
                  className="w-full text-destructive hover:text-destructive"
                  onClick={() => actions.removeNode(selectedNode.id)}
                >
                  <Trash2 className="mr-1.5 h-3.5 w-3.5" />
                  Remove node
                </Button>
              )}
            </>
          ) : selectedEdge ? (
            <div className="space-y-3">
              <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Connection</p>
              <p className="text-sm">
                {nodeLabel(selectedEdge.source)} · {selectedEdge.sourceHandle}
                <span className="text-muted-foreground"> → </span>
                {nodeLabel(selectedEdge.target)} · {selectedEdge.targetHandle}
              </p>
              <Button
                variant="outline"
                size="sm"
                className="w-full text-destructive hover:text-destructive"
                onClick={() => actions.removeEdge(selectedEdge.id)}
              >
                <Trash2 className="mr-1.5 h-3.5 w-3.5" />
                Remove connection
              </Button>
            </div>
          ) : (
            <div className="space-y-3">
              <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Flow</p>
              <p className="text-xs text-muted-foreground">
                Select a node to bind its inputs and set its parameters. Modules reuse a result
                they already computed with the same inputs and parameters.
              </p>
              {issues.length > 0 && (
                <div className="space-y-1.5">
                  <p className="text-xs font-medium text-destructive">
                    {issues.length} problem{issues.length === 1 ? "" : "s"}
                  </p>
                  <ul className="space-y-1">
                    {issues.map((it, i) => (
                      <li key={i}>
                        <button
                          type="button"
                          disabled={!it.node_id}
                          onClick={() => it.node_id && actions.selectNode(it.node_id)}
                          className="text-left text-xs text-destructive enabled:hover:underline"
                        >
                          {it.node_id ? `${nodeLabel(it.node_id)}: ` : ""}
                          {it.message}
                        </button>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          )}
        </div>
      </ScrollArea>
    </div>
  );
}
