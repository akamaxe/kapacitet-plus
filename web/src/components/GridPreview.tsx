import { useCallback } from "react";
import type { Map as MLMap } from "maplibre-gl";
import { useApi } from "../lib/api";
import { tokens } from "../lib/colors";
import { useT } from "../lib/i18n";
import { useStore } from "../lib/store";
import { MapView, fitTo } from "./MapView";
import { Icon } from "./Icon";

/** Geographic preview of the actual project model; colours identify assets, not loading. */
export function GridPreview() {
  const t = useT();
  const theme = useStore((s) => s.theme);
  const net = useApi<any>("/network");
  const meta = useApi<any>("/meta");
  const setup = useCallback((map: MLMap) => {
    if (!net.data) return;
    const tk = tokens();
    if (map.getSource("preview")) return;
    map.addSource("preview", { type: "geojson", data: net.data });
    map.addLayer({ id: "preview-context", type: "fill", source: "preview", filter: ["==", ["get", "layer"], "context"], paint: { "fill-color": tk.borderStrong, "fill-opacity": 0.18 } });
    map.addLayer({ id: "preview-cables", type: "line", source: "preview", filter: ["==", ["get", "layer"], "cable"], paint: { "line-color": tk.accent, "line-width": 1.5, "line-opacity": 0.8 } });
    map.addLayer({ id: "preview-homes", type: "fill-extrusion", source: "preview", filter: ["==", ["get", "layer"], "house"], paint: { "fill-extrusion-color": tk.accent, "fill-extrusion-height": 9, "fill-extrusion-opacity": 0.55 } });
    map.addLayer({ id: "preview-sub", type: "circle", source: "preview", filter: ["==", ["get", "layer"], "substation"], paint: { "circle-color": tk.cat[2], "circle-radius": 5, "circle-stroke-width": 2, "circle-stroke-color": tk.text } });
  }, [net.data, theme]);
  return (
    <div className="grid-preview">
      {net.data && meta.data ? <MapView center={meta.data.center} zoom={15.5} pitch={45} bearing={-25} basemap={false} interactive={false} setup={setup} onReady={(map) => { fitTo(map, net.data.bbox, 42); map.on("resize", () => fitTo(map, net.data.bbox, 42)); }} ariaLabel={t("overview.mapLabel")} /> : <div className="preview-placeholder"><Icon name="twin" size={48} /><span>{t(net.isError || meta.isError ? "overview.mapUnavailable" : "ui.loading")}</span></div>}
      <div className="preview-heading"><span className="eyebrow">{t("overview.twin")}</span><span className="preview-location">Smedslätten / SE3</span></div>
      <a className="preview-open" href="#/twin"><span><Icon name="twin" size={16} />{t("overview.explore")}</span></a>
    </div>
  );
}
