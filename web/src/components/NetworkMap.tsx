import { useCallback, useRef, useState } from "react";
import type { Map as MLMap, MapMouseEvent } from "maplibre-gl";
import { useApi } from "../lib/api";
import { tokens } from "../lib/colors";
import { useT } from "../lib/i18n";
import { useStore } from "../lib/store";
import { Mark } from "./bits";
import { Icon } from "./Icon";
import { MapView, fitTo } from "./MapView";

type Tip = { x: number; y: number; cid: number; address: string; status: string };

/**
 * The network on the real map, in 3D: the houses OpenStreetMap maps, raised by their floors and
 * coloured by how their solar is connected, the synthetic cables in ink and the substation where
 * OpenStreetMap places it. Drag to move and turn it; the wheel zooms only with Ctrl held, so the
 * page around it still scrolls. A house opens a connection request for it.
 */
export function NetworkMap() {
  const t = useT();
  const theme = useStore((s) => s.theme);
  const lang = useStore((s) => s.lang);
  const net = useApi<any>("/network");
  const meta = useApi<any>("/meta");
  const mapRef = useRef<MLMap | null>(null);
  const [tip, setTip] = useState<Tip | null>(null);

  const houses: any[] = net.data?.features.filter((x: any) => x.properties.layer === "house") ?? [];
  const feeders = new Set(houses.map((h) => h.properties.feeder)).size;

  // the box holds the whole drawing; tilted, the houses read small, so the view steps in a little
  const frameView = (map: MLMap) => {
    if (!net.data?.bbox) return;
    fitTo(map, net.data.bbox, 24);
    map.setZoom(map.getZoom() + 0.35);
  };

  const setup = useCallback(
    (map: MLMap) => {
      if (!net.data) return;
      const tk = tokens();
      const fc = (features: any[]) => ({ type: "FeatureCollection", features });
      const of = (layer: string) => net.data.features.filter((x: any) => x.properties.layer === layer);
      const raised = of("house").map((h: any) => ({ ...h, properties: { ...h.properties, height: Math.max(1, h.properties.levels) * 3.2 } }));
      const add = (id: string, data: any, promoteId?: string) => {
        if (!map.getSource(id)) map.addSource(id, { type: "geojson", data, ...(promoteId ? { promoteId } : {}) } as any);
      };
      add("net-context", fc(of("context")));
      add("net-cables", fc(of("cable")));
      add("net-houses", fc(raised), "cid");
      add("net-sub", fc(of("substation")));
      if (map.getLayer("net-houses")) return;
      map.addLayer({ id: "net-context", type: "fill", source: "net-context", paint: { "fill-color": tk.text2, "fill-opacity": 0.1 } });
      map.addLayer({
        id: "net-cables", type: "line", source: "net-cables",
        layout: { "line-cap": "round", "line-join": "round" },
        paint: {
          "line-color": tk.text,
          "line-opacity": 0.85,
          // zoom must be the top-level input of interpolate; the cable kind is decided inside
          "line-width": ["interpolate", ["linear"], ["zoom"],
            14, ["case", ["==", ["get", "kind"], "main"], 1.6, 0.6],
            18, ["case", ["==", ["get", "kind"], "main"], 5, 1.6]],
        },
      });
      map.addLayer({
        id: "net-houses", type: "fill-extrusion", source: "net-houses",
        paint: {
          "fill-extrusion-color": ["match", ["get", "pv_status"], "firm", tk.goodFill, "nonfirm", tk.warningFill, tk.borderStrong],
          "fill-extrusion-height": ["get", "height"],
          "fill-extrusion-opacity": 0.92,
        },
      });
      map.addLayer({
        id: "net-houses-hover", type: "line", source: "net-houses",
        paint: { "line-color": tk.text, "line-width": ["case", ["boolean", ["feature-state", "hover"], false], 2, 0] },
      });
      map.addLayer({
        id: "net-sub", type: "circle", source: "net-sub",
        paint: { "circle-radius": 8, "circle-color": tk.surface, "circle-stroke-width": 3.5, "circle-stroke-color": tk.accent },
      });
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [net.data, theme],
  );

  const onReady = useCallback(
    (map: MLMap) => {
      mapRef.current = map;
      frameView(map);
      map.on("click", "net-houses", (e: MapMouseEvent & { features?: any[] }) => {
        const cid = e.features?.[0]?.properties?.cid;
        if (cid == null) return;
        useStore.getState().select(Number(cid));
        useStore.getState().setScreen("request");
      });
      // hover is read once per frame, and touches feature state only when the house changes
      let hovered: number | null = null;
      let point: { x: number; y: number } | null = null;
      let frame = 0;
      const mark = (cid: number | null) => {
        if (cid === hovered) return;
        if (hovered !== null) map.setFeatureState({ source: "net-houses", id: hovered }, { hover: false });
        if (cid !== null) map.setFeatureState({ source: "net-houses", id: cid }, { hover: true });
        hovered = cid;
      };
      const read = () => {
        frame = 0;
        if (!point || !map.getLayer("net-houses")) return;
        const ft = map.queryRenderedFeatures([point.x, point.y], { layers: ["net-houses"] })[0];
        const cid = ft ? Number(ft.properties.cid) : null;
        mark(cid);
        map.getCanvas().style.cursor = ft ? "pointer" : "";
        setTip(ft && cid !== null ? { x: point.x, y: point.y, cid, address: ft.properties.address ?? "", status: ft.properties.pv_status ?? "none" } : null);
      };
      map.on("mousemove", (e: MapMouseEvent) => {
        point = { x: e.point.x, y: e.point.y };
        if (!frame) frame = requestAnimationFrame(read);
      });
      map.on("mouseout", () => {
        point = null;
        mark(null);
        setTip(null);
      });
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [net.data],
  );

  const reset = () => {
    const map = mapRef.current;
    if (!map) return;
    map.jumpTo({ pitch: 50, bearing: -18 });
    frameView(map);
  };

  return (
    <figure className="net-figure">
      <figcaption className="net-head">
        <strong>{t("net.title")}</strong>
        <span>{t("net.sub", { n: houses.length || "–", f: feeders || "–" })}</span>
      </figcaption>
      <div className="net-map">
        {net.data && meta.data ? (
          // the gesture hint is written into the map when it is made, so a new language makes a new map
          <MapView
            key={lang}
            center={meta.data.center}
            zoom={16}
            pitch={50}
            bearing={-18}
            setup={setup}
            onReady={onReady}
            gestureHelp={{ windows: t("net.gestures.windows"), mac: t("net.gestures.mac"), mobile: t("net.gestures.mobile") }}
            ariaLabel={t("net.aria")}
          >
            <div className="map-toolbar">
              <button className="icon-btn" aria-label={t("twin.resetView")} title={t("twin.resetView")} onClick={reset}>
                <Icon name="reset" size={17} />
              </button>
            </div>
            {tip && (
              <div className="map-tooltip" style={{ transform: `translate(${tip.x + 14}px, ${tip.y + 14}px)` }}>
                <div className="tt-title">{tip.address || t("dt.unnamed", { id: tip.cid })}</div>
                <div className="tt-row"><span>{t("dt.status")}</span><b>{t(`twin.status.${tip.status}`)}</b></div>
                <div className="net-act">{t("net.test")}</div>
              </div>
            )}
          </MapView>
        ) : (
          <div className="net-empty">{t(net.isError || meta.isError ? "overview.mapUnavailable" : "ui.loading")}</div>
        )}
      </div>
      <div className="net-key">
        <span><i className="house-key firm" />{t("net.firm")}</span>
        <span><i className="house-key nonfirm" />{t("net.nonfirm")}</span>
        <span><i className="house-key" />{t("twin.status.none")}</span>
        <span className="net-src"><Mark kind="real" />{t("net.srcReal")}</span>
        <span className="net-src"><Mark kind="synthetic" />{t("net.srcSyn")}</span>
      </div>
    </figure>
  );
}
