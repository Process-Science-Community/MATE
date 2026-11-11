"use client";

import { useMemo, useState } from "react";
import { Workflow } from "lucide-react";

import {
  CanvasSettings,
  CanvasSettingsSelect,
} from "@/components/visualizations/canvases/shared/canvas-toolbar";
import { PetriNetView, type PetriNetViewNet } from "@/components/visualizations/artifacts/petri-net";
import { EmptyState } from "@/components/empty-state";
import { DEFAULT_PETRI, useVizSettings } from "@/lib/stores/visualization-settings";

import type { OcpnData, OcpnNet } from "../queries";

/** Initial / final places carry a token dot on top of their ring, so the
 *  marking is legible without a legend. */
const OCPN_PETRI = { ...DEFAULT_PETRI, placeMode: "count" as const };
const EMPTY_NET: PetriNetViewNet = { places: [], transitions: [], arcs: [] };

function toViewNet(net: OcpnNet): PetriNetViewNet {
  return {
    places: net.places.map((p) => ({ ...p, tokens: p.is_initial || p.is_final ? 1 : 0 })),
    transitions: net.transitions.map((t) => ({ id: t.id, label: t.label, is_invisible: t.silent })),
    arcs: net.arcs,
  };
}

/** Object-centric Petri net for a single object type, rendered by the platform
 *  `PetriNetView`. Variable arcs – where an activity consumes / produces a
 *  variable number of objects – are drawn thicker, mirroring pm4py's own OCPN
 *  visualiser. */
export function OcpnCanvas({ data }: { data: OcpnData }) {
  const general = useVizSettings((s) => s.general);
  // The object-type picker is a canvas control – it lives in the settings
  // popover, so switching types keeps the popover open.
  const [ot, setOt] = useState<string | null>(null);
  const objectType = ot ?? data.object_types[0] ?? null;
  const net = data.nets.find((n) => n.object_type === objectType) ?? null;
  const viewNet = useMemo(() => (net ? toViewNet(net) : null), [net]);
  const variableArcs = useMemo(
    () => new Set(net?.arcs.filter((a) => a.variable).map((a) => a.id)),
    [net],
  );

  const settings = (
    <CanvasSettings>
      <CanvasSettingsSelect
        label="Object type"
        value={objectType ?? ""}
        onChange={setOt}
        options={data.object_types.map((t) => ({ value: t, label: t }))}
      />
    </CanvasSettings>
  );

  // No net for this type: the same view over an empty net, so the canvas (and
  // its open picker / fullscreen) stays mounted, with the reason on top.
  const shown = viewNet?.places.length ? viewNet : null;

  return (
    <PetriNetView
      net={shown ?? EMPTY_NET}
      petri={OCPN_PETRI}
      general={shown ? general : { ...general, showMinimap: false }}
      arcWidth={(arc) => (arc.id && variableArcs.has(arc.id) ? 3 : 1.5)}
      fitViewKey={`ocpn-${objectType}`}
      settings={settings}
      overlay={
        shown ? undefined : (
          <div className="absolute inset-0 flex items-center justify-center">
            <EmptyState
              icon={Workflow}
              title="No Petri net for this object type"
              description="Too few events to mine a model. Pick another object type in the canvas settings."
            />
          </div>
        )
      }
    />
  );
}
