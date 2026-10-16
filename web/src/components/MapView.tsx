import { useEffect, useRef } from "react";
import * as maplibregl from "maplibre-gl";
import type { Map as MLMap, StyleSpecification } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
// MapLibre 6 loads its worker from a file next to its own module, which a bundler does not emit.
// Let Vite bundle the worker (with its shared chunk) and tell MapLibre where it is.
import workerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url";

maplibregl.setWorkerUrl(workerUrl);
import { isDark, useStore } from "../lib/store";
import { token } from "../lib/colors";

const BASEMAP = {
  light: "https://tiles.openfreemap.org/styles/positron",
  dark: "https://tiles.openfreemap.org/styles/dark",
};

/** Plain background style used when the basemap cannot be reached (offline demo). */
function offlineStyle(): StyleSpecification {
  return {
    version: 8,
    sources: {},
    layers: [{ id: "bg", type: "background", paint: { "background-color": token("--bg-base") } }],
  };
}

export interface MapViewProps {
  center: [number, number];
  zoom: number;
  pitch?: number;
  bearing?: number;
  /** Called after every style load: add sources and layers here. */
  setup: (map: MLMap) => void;
  /** Called once, right after the first `setup` (it does not wait for basemap tiles). */
  onReady?: (map: MLMap) => void;
  interactive?: boolean;
  basemap?: boolean;
  children?: React.ReactNode;
  ariaLabel: string;
}

export function MapView({ center, zoom, pitch = 0, bearing = 0, setup, onReady, interactive = true, basemap = true, children, ariaLabel }: MapViewProps) {
  const ref = useRef<HTMLDivElement>(null);
  const mapRef = useRef<MLMap | null>(null);
  const setupRef = useRef(setup);
  setupRef.current = setup;
  const onReadyRef = useRef(onReady);
  onReadyRef.current = onReady;
  const theme = useStore((s) => s.theme);
  const offlineRef = useRef(!basemap);
  const themeRef = useRef(theme);

  useEffect(() => {
    if (!ref.current) return;
    // true once a style (basemap or fallback) has been parsed; slow tiles do not count as offline
    let styleReady = false;
    let readyCalled = false;
    const map = new maplibregl.Map({
      container: ref.current,
      style: basemap ? (isDark() ? BASEMAP.dark : BASEMAP.light) : offlineStyle(),
      center,
      zoom,
      pitch,
      bearing,
      interactive,
      attributionControl: false,
      maxPitch: 70,
    });
    map.addControl(new maplibregl.AttributionControl({
      compact: true,
      customAttribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors · <a href="https://openfreemap.org">OpenFreeMap</a>',
    }));
    // MapLibre opens the compact attribution once, when the first attributions arrive; close it
    // right then so it does not cover the map (the (i) button still opens it)
    const attrib = ref.current.querySelector(".maplibregl-ctrl-attrib");
    const attribObserver = new MutationObserver(() => {
      if (attrib?.classList.contains("maplibregl-compact-show")) {
        attrib.classList.remove("maplibregl-compact-show");
        attribObserver.disconnect();
      }
    });
    if (attrib) attribObserver.observe(attrib, { attributes: true, attributeFilter: ["class"] });
    if (interactive) map.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), "top-left");
    mapRef.current = map;
    (window as any).__kapMap = map; // handy when inspecting the demo in the browser console
    const onStyle = () => {
      styleReady = true;
      try {
        // our own modelled houses replace the basemap buildings (same OSM footprints)
        for (const layer of map.getStyle().layers ?? []) {
          if (/building/i.test(layer.id)) map.setLayoutProperty(layer.id, "visibility", "none");
        }
        setupRef.current(map);
        // interaction handlers and the initial camera must not wait for basemap tiles (slow network)
        if (!readyCalled) {
          readyCalled = true;
          onReadyRef.current?.(map);
        }
      } catch (err) {
        console.error("[kapacitet] map setup failed", err);
      }
    };
    map.on("style.load", onStyle);
    // If the basemap style cannot be fetched (offline), fall back to a plain background.
    const fallback = () => {
      if (offlineRef.current || styleReady) return;
      offlineRef.current = true;
      map.setStyle(offlineStyle());
    };
    map.on("error", (e: any) => {
      if (/fetch|Failed|NetworkError|style/i.test(String(e?.error?.message ?? ""))) fallback();
    });
    const timer = window.setTimeout(fallback, 8000);
    return () => {
      window.clearTimeout(timer);
      attribObserver.disconnect();
      map.remove();
      mapRef.current = null;
    };
  }, []);

  // Switch basemap with the theme (not on mount: the constructor already loaded the right one);
  // custom layers are re-added by `setup` on style.load.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || themeRef.current === theme) return;
    themeRef.current = theme;
    requestAnimationFrame(() => map.setStyle(offlineRef.current ? offlineStyle() : isDark() ? BASEMAP.dark : BASEMAP.light));
  }, [theme]);

  return (
    <div className="map" role="application" aria-label={ariaLabel}>
      <div ref={ref} style={{ position: "absolute", inset: 0 }} />
      {children}
    </div>
  );
}

export function fitTo(map: MLMap, bbox: [number, number, number, number], padding = 40) {
  map.fitBounds([[bbox[0], bbox[1]], [bbox[2], bbox[3]]], { padding, duration: 0 });
}
