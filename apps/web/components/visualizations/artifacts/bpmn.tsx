"use client";

/**
 * `BpmnView` – the one bpmn-js viewer wrapper (view-only `NavigatedViewer` +
 * auto-layout + idle-fading minimap + smooth zoom + the shared control
 * cluster) behind discovery's BPMN tab, conformance's deviation map, process
 * comparison's side-by-side models and `bpmn` artifact cards. Runtime external.
 *
 * The bpmn-js libraries are passed in as `engine`, never imported here: the
 * host runs `next dev --turbo`, and Turbopack mis-bundles bpmn-moddle so every
 * importXML/layoutProcess fails with "failed to parse document as
 * <bpmn:Definitions>". A module panel bundles them with esbuild (which handles
 * bpmn-moddle correctly) and hands its copies in – see `BpmnEngine`.
 *
 * bpmn-js CSS is loaded by the host app (apps/web/app/layout.tsx).
 */

import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { AlertTriangle } from "lucide-react";

import { EmptyState } from "@/components/empty-state";
import {
  useCanvasIdleVisibility,
  useFullscreen,
  useSmoothBpmnZoom,
  type BpmnZoomCanvas,
} from "@/components/visualizations/canvases/shared/canvas-controls";
import {
  CanvasBusyChip,
  CanvasControlCluster,
  type CanvasResetSlotProps,
} from "@/components/visualizations/canvases/shared/canvas-toolbar";

/** A bpmn-js viewer: diagram-js services resolved off its DI container. */
export type BpmnViewer = { get<T = unknown>(name: string): T };

/**
 * The bpmn-js pieces a caller bundles itself:
 *
 * ```ts
 * import NavigatedViewer from "bpmn-js/lib/NavigatedViewer";
 * import { layoutProcess } from "bpmn-auto-layout";
 * import minimapModule from "diagram-js-minimap";
 * const BPMN_ENGINE: BpmnEngine = { Viewer: NavigatedViewer, layoutProcess, minimapModule };
 * ```
 *
 * `NavigatedViewer` is the strictly view-only entry: pan + scroll-zoom, NO
 * editing services.
 */
export interface BpmnEngine {
  // `never`: accepts any bpmn-js version's constructor; the view passes only
  // `container`, `additionalModules` and `bpmnRenderer`.
  Viewer: new (options: never) => unknown;
  layoutProcess: (xml: string) => Promise<string>;
  minimapModule: unknown;
}

type ViewerHandle = BpmnViewer & {
  destroy: () => void;
  importXML: (xml: string) => Promise<unknown>;
};

// -- Theme + minimap styles --------------------------------------------------

/**
 * `bpmnRenderer` config making the diagram follow the app theme. bpmn-js writes
 * each element's colour into its **inline style** at import time, so no
 * stylesheet can re-theme a literal colour; handing it CSS variables instead
 * lets `BASE_CSS` re-point them per theme – the canvas re-themes live on a
 * theme toggle without a rebuild. A colour the model declares itself (BPMN DI)
 * still wins.
 */
const BPMN_THEME_COLORS = {
  defaultFillColor: "var(--ff-bpmn-fill)",
  defaultStrokeColor: "var(--ff-bpmn-stroke)",
  defaultLabelColor: "var(--ff-bpmn-label)",
} as const;

/** bpmn-js' own default stroke/fill (`BpmnRenderUtil`'s `black` / `white`). */
export const BPMN_BLACK = "hsl(225, 10%, 15%)";
const BPMN_WHITE = "white";

// Light mode restates bpmn-js' defaults; dark swaps in the app tokens.
// diagram-js-minimap ships a top-right, light-only panel with an orange
// viewport box: restyled to match the React Flow minimap (bottom-right, card
// surface, neutral viewport), faded via `ff-minimap-hidden`, toggle hidden.
const BASE_CSS = `
.djs-container{--ff-bpmn-fill:${BPMN_WHITE};--ff-bpmn-stroke:${BPMN_BLACK};--ff-bpmn-label:${BPMN_BLACK};}
.dark .djs-container{--ff-bpmn-fill:var(--card);--ff-bpmn-stroke:var(--foreground);--ff-bpmn-label:var(--foreground);}
.djs-element.ff-bpmn-hit .djs-outline{stroke:#f59e0b!important;stroke-width:3px!important;stroke-dasharray:none!important;display:block!important;}
.djs-minimap{top:auto!important;bottom:15px!important;right:15px!important;background:var(--card)!important;border:1px solid var(--border)!important;border-radius:6px!important;box-shadow:0 1px 2px rgba(0,0,0,.08)!important;transition:opacity .3s ease!important;}
.djs-minimap .map{width:200px;height:150px;}
.djs-minimap .toggle{display:none!important;}
.djs-minimap .viewport-dom{border:2px solid rgba(0,0,0,.4)!important;border-radius:3px!important;}
.dark .djs-minimap .viewport-dom{border-color:rgba(255,255,255,.5)!important;}
.djs-minimap.ff-minimap-hidden{opacity:0!important;pointer-events:none!important;}
`;

const STYLE_ID = "ff-bpmn-view-styles";

function injectBaseStyles(): void {
  if (typeof document === "undefined" || document.getElementById(STYLE_ID)) return;
  const style = document.createElement("style");
  style.id = STYLE_ID;
  style.textContent = BASE_CSS;
  document.head.appendChild(style);
}

// -- Search -------------------------------------------------------------------

interface BElement {
  id: string;
  type: string;
  businessObject?: { name?: string };
}

/**
 * Find a task by (case-insensitive, substring) name and centre it, with a
 * transient highlight. Returns false when nothing matches.
 */
function locateActivity(viewer: BpmnViewer, query: string): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return false;
  const registry = viewer.get<{ getAll(): BElement[] }>("elementRegistry");
  const canvas = viewer.get<{
    addMarker(id: string, cls: string): void;
    removeMarker(id: string, cls: string): void;
    scrollToElement(el: BElement): void;
  }>("canvas");

  const hit = registry
    .getAll()
    .find(
      (el) =>
        (/Task$/.test(el.type) || el.type === "bpmn:SubProcess") &&
        el.businessObject?.name?.toLowerCase().includes(q),
    );
  if (!hit) return false;

  canvas.scrollToElement(hit);
  canvas.addMarker(hit.id, "ff-bpmn-hit");
  window.setTimeout(() => {
    try {
      canvas.removeMarker(hit.id, "ff-bpmn-hit");
    } catch {
      // canvas torn down before the timeout fired — ignore.
    }
  }, 2200);
  return true;
}

// -- View -----------------------------------------------------------------------

export interface BpmnViewProps extends CanvasResetSlotProps {
  engine: BpmnEngine;
  /** BPMN XML. Re-imported in place when it changes, so the canvas – and the
   *  settings popover the user just changed something in – stays mounted. */
  xml: string;
  /** Applies the owner's markers/overlays. Re-run after every import and
   *  whenever its identity changes – memoize it on the data it reads. */
  decorate?: (viewer: BpmnViewer) => void;
  /** Task search term; centred + highlighted whenever `searchNonce` bumps. */
  searchQuery?: string;
  searchNonce?: number;
  onSearchResult?: (found: boolean) => void;
  /** Bump to re-fit after an external container resize (bpmn-js doesn't
   *  re-fit on resize). Skips the mount run. */
  refitKey?: string | number;
  /** Popover body of the canvas control cluster. */
  settings?: ReactNode;
  /** A re-mine is in flight while the previous model stays on screen. */
  busy?: boolean;
  busyLabel?: string;
}

export function BpmnView({
  engine,
  xml,
  decorate,
  searchQuery,
  searchNonce,
  onSearchResult,
  refitKey,
  settings,
  busy = false,
  busyLabel,
  onReset,
  resetLabel,
  resetTitle,
  resetDescription,
  resetConfirmLabel,
}: BpmnViewProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const viewerRef = useRef<ViewerHandle | null>(null);
  const [ready, setReady] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Bumped once the viewer instance exists so the import effect can run.
  const [viewerNonce, setViewerNonce] = useState(0);
  // Bumped after every successful import – a fresh diagram has no decoration.
  const [importNonce, setImportNonce] = useState(0);
  // The minimap is opened / wired exactly once per viewer, not per import.
  const minimapWired = useRef(false);
  const onSearchResultRef = useRef(onSearchResult);
  onSearchResultRef.current = onSearchResult;
  const engineRef = useRef(engine);
  engineRef.current = engine;

  const rootRef = useRef<HTMLDivElement>(null);
  const { isFullscreen, toggle: toggleFullscreen } = useFullscreen(rootRef);
  // Minimap hidden by default; fades in only while the user pans/zooms.
  const { visible: minimapVisible, notifyActivity } = useCanvasIdleVisibility({ idleMs: 1200 });
  // Tweened buttons + continuous cursor-anchored pinch zoom (reduced-motion
  // aware), replacing diagram-js's snap-to-step zoom.
  const getZoomCanvas = useCallback(() => viewerRef.current?.get<BpmnZoomCanvas>("canvas") ?? null, []);
  const { zoomIn, zoomOut, fit, fitNow } = useSmoothBpmnZoom(getZoomCanvas, containerRef, {
    onActivity: notifyActivity,
  });

  // Create the viewer once. Importing is a separate effect so a new `xml`
  // swaps the model in place instead of tearing the canvas down.
  useEffect(() => {
    injectBaseStyles();
    const container = containerRef.current;
    if (!container) return;

    const viewer = new engineRef.current.Viewer({
      container,
      additionalModules: [engineRef.current.minimapModule],
      bpmnRenderer: BPMN_THEME_COLORS,
    } as never) as ViewerHandle;
    viewerRef.current = viewer;
    minimapWired.current = false;
    setViewerNonce((n) => n + 1);

    return () => {
      viewerRef.current = null;
      setReady(false);
      try {
        viewer.destroy();
      } catch {
        /* ignore – destroy can throw if import never completed */
      }
    };
  }, []);

  useEffect(() => {
    const viewer = viewerRef.current;
    if (!viewer || viewerNonce === 0) return;

    let cancelled = false;
    (async () => {
      try {
        // pm4py emits BPMN without BPMNDI (no coordinates); bpmn-auto-layout
        // fills them in. Files that already have DI pass through unchanged.
        let laidOut = xml;
        try {
          laidOut = await engineRef.current.layoutProcess(xml);
        } catch {
          /* keep the original – it may already carry DI */
        }
        if (cancelled || viewerRef.current !== viewer) return;
        await viewer.importXML(laidOut);
        if (cancelled || viewerRef.current !== viewer) return;
        // Instant, not tweened: the diagram should appear already framed.
        fitNow();
        // Open the minimap (shown/hidden via the `ff-minimap-hidden` opacity
        // class) and pump activity on every viewbox change. Once per viewer –
        // a re-import keeps the eventBus, so re-wiring would stack listeners.
        if (!minimapWired.current) {
          minimapWired.current = true;
          try {
            viewer.get<{ open: () => void }>("minimap").open();
            viewer
              .get<{ on: (e: string, cb: () => void) => void }>("eventBus")
              .on("canvas.viewbox.changed", () => notifyActivity());
          } catch {
            /* minimap is best-effort – never block the canvas on it */
          }
        }
      } catch (err) {
        // Surface import failures instead of a blank canvas.
        console.error("BpmnView: importXML failed", err);
        if (!cancelled) setError((err as Error)?.message ?? "Failed to render BPMN");
        return;
      }
      if (!cancelled) {
        setError(null);
        setReady(true);
        setImportNonce((n) => n + 1);
      }
    })();

    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [viewerNonce, xml]);

  // Re-apply the owner's decoration after each import or when it changes.
  useEffect(() => {
    const viewer = viewerRef.current;
    if (!ready || !viewer || !decorate) return;
    try {
      decorate(viewer);
    } catch (err) {
      console.error("BpmnView: decorate failed", err);
    }
  }, [ready, importNonce, decorate]);

  // Locate a task on demand.
  useEffect(() => {
    const viewer = viewerRef.current;
    if (!ready || !viewer || !searchNonce || !searchQuery) return;
    onSearchResultRef.current?.(locateActivity(viewer, searchQuery));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchNonce]);

  // Re-fit on fullscreen enter/exit or an external resize. Skip mount – the
  // import already frames the diagram.
  const didMountFit = useRef(false);
  useEffect(() => {
    if (!didMountFit.current) {
      didMountFit.current = true;
      return;
    }
    const id = requestAnimationFrame(() => {
      // diagram-js caches the viewport size with the viewbox and only drops it
      // on a viewbox change or `resized()` – without this the fit frames the
      // diagram inside the pre-resize box (top third of a fullscreen canvas).
      viewerRef.current?.get<{ resized(): void }>("canvas").resized();
      fitNow();
    });
    return () => cancelAnimationFrame(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isFullscreen, refitKey]);

  // Opacity gate for the minimap – toggled by interaction activity.
  useEffect(() => {
    containerRef.current
      ?.querySelector(".djs-minimap")
      ?.classList.toggle("ff-minimap-hidden", !minimapVisible);
  }, [minimapVisible, ready]);

  if (error) {
    return <EmptyState icon={AlertTriangle} title="Could not render BPMN" description={error} />;
  }

  return (
    <div
      ref={rootRef}
      className={`relative h-full w-full${isFullscreen ? " bg-background" : ""}`}
      onWheelCapture={notifyActivity}
      onPointerDownCapture={notifyActivity}
    >
      <div ref={containerRef} className="h-full w-full" />
      {/* Same cluster as every React-Flow canvas – top-right, minimap owns
          bottom-right. */}
      <CanvasControlCluster
        onZoomIn={zoomIn}
        onZoomOut={zoomOut}
        onFit={fit}
        isFullscreen={isFullscreen}
        onToggleFullscreen={toggleFullscreen}
        settings={settings}
        // The owner restores its own view state; re-framing is ours.
        onReset={
          onReset
            ? () => {
                onReset();
                fit();
              }
            : undefined
        }
        resetLabel={resetLabel}
        resetTitle={resetTitle}
        resetDescription={resetDescription}
        resetConfirmLabel={resetConfirmLabel}
      />
      {busy && <CanvasBusyChip label={busyLabel} />}
    </div>
  );
}
