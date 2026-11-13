import { useEffect, useRef, useState } from "react";
import * as maplibregl from "maplibre-gl";
import type { Map as MLMap, StyleSpecification } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
// MapLibre 6 loads its worker from a file next to its own module, which a bundler does not emit.
// Let Vite bundle the worker (with its shared chunk) and tell MapLibre where it is.
import workerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url";

maplibregl.setWorkerUrl(workerUrl);
import { isDark, useStore } from "../lib/store";
import { useT } from "../lib/i18n";
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
  /**
   * A map inside a page that scrolls: the wheel scrolls the page and zooms the map only with
   * Ctrl or Cmd held, and a finger pans the page, two fingers the map. These are the words
   * MapLibre shows when it holds a gesture back.
   */
  gestureHelp?: { windows: string; mac: string; mobile: string };
  children?: React.ReactNode;
  ariaLabel: string;
}

export function MapView({ center, zoom, pitch = 0, bearing = 0, setup, onReady, interactive = true, basemap = true, gestureHelp, children, ariaLabel }: MapViewProps) {
  const ref = useRef<HTMLDivElement>(null);
  const mapRef = useRef<MLMap | null>(null);
  const setupRef = useRef(setup);
  setupRef.current = setup;
  const onReadyRef = useRef(onReady);
  onReadyRef.current = onReady;
  const theme = useStore((s) => s.theme);
  const offlineRef = useRef(!basemap);
  // the basemap was wanted and could not be fetched: the modelled layers still draw, on a plain ground
  const [offline, setOffline] = useState(false);
  const t = useT();
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
      ...(gestureHelp
        ? {
            cooperativeGestures: true,
            locale: {
              "CooperativeGesturesHandler.WindowsHelpText": gestureHelp.windows,
              "CooperativeGesturesHandler.MacHelpText": gestureHelp.mac,
              "CooperativeGesturesHandler.MobileHelpText": gestureHelp.mobile,
            },
          }
        : {}),
    });
    // the credit belongs to the basemap: a drawing on an empty ground shows nothing of theirs
    if (basemap) map.addControl(new maplibregl.AttributionControl({
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
      setOffline(true);
      map.setStyle(offlineStyle());
    };
    map.on("error", (e: any) => {
      if (/fetch|Failed|NetworkError|style/i.test(String(e?.error?.message ?? ""))) fallback();
    });
    const timer = window.setTimeout(fallback, 8000);
    // MapLibre follows the window's size, not its own box's: opening or folding the project tree
    // changes the box alone, and the map would keep drawing for the old one
    const box = new ResizeObserver(() => map.resize());
    box.observe(ref.current);
    return () => {
      window.clearTimeout(timer);
      box.disconnect();
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
      {offline && basemap && <p className="map-notice" role="status">{t("map.offline")}</p>}
    </div>
  );
}

/** Frame a box without undoing the tilt and turn the view was given: a 3D view stays 3D. */
export function fitTo(map: MLMap, bbox: [number, number, number, number], padding = 40) {
  map.fitBounds([[bbox[0], bbox[1]], [bbox[2], bbox[3]]], { padding, duration: 0, bearing: map.getBearing(), pitch: map.getPitch() });
}

/** The positions of the houses of a network answer, for keepInView. */
export function housePoints(features: any[] | undefined): [number, number][] {
  const out: [number, number][] = [];
  for (const ft of features ?? []) {
    const { layer, lon, lat } = ft.properties ?? {};
    if (layer === "house" && Number.isFinite(lon) && Number.isFinite(lat)) out.push([lon, lat]);
  }
  return out;
}

/**
 * Step the view out until every point is on the canvas, `margin` pixels inside its edge. A box is
 * framed as if seen from above, so a tilted view of a long, thin network cuts its ends off. A
 * network about as wide as it is deep, as the reference one, is inside already or one step from it.
 */
export function keepInView(map: MLMap, points: [number, number][], margin = 28, steps = 30) {
  const box = map.getContainer();
  const w = box.clientWidth;
  const h = box.clientHeight;
  if (!w || !h || !points.length) return;
  const outside = () => points.some((at) => {
    const p = map.project(at);
    return p.x < margin || p.y < margin || p.x > w - margin || p.y > h - margin;
  });
  for (let i = 0; i < steps && outside(); i++) map.setZoom(map.getZoom() - 0.1);
}
