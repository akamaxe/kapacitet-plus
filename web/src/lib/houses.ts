/**
 * A house's connection as the screens show it. Where the envelopes are on: by the envelope
 * deployment of the showcase, firm, on an envelope, or no solar. Where they are off: by the hourly
 * study's deployment (GET /hourly, src/kapacitet/engine/hourly.py), firm by the static study, added
 * by the hourly study, or no solar. The map, the tree, the search and the panels all read it here.
 */
import { useEffect, useRef } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useApi } from "./api";
import { useEnvelopeView, useFeaturesKnown } from "./features";

export type HouseStatus = "firm" | "nonfirm" | "static" | "hourly" | "none";

/** What a /network house says of itself, as far as its status goes. */
export interface HouseProps {
  pv_status?: string | null;
  hourly_status?: string | null;
}

/** The status a house shows on this installation. Pure. */
export function houseStatus(p: HouseProps, envelopes: boolean): HouseStatus {
  if (envelopes) return p.pv_status === "firm" || p.pv_status === "nonfirm" ? p.pv_status : "none";
  return p.hourly_status === "static" || p.hourly_status === "hourly" ? p.hourly_status : "none";
}

/**
 * The hourly study's deployment, asked where the envelopes are off. The server computes it the first
 * time (seconds), and once it is there /network is asked again, so each house carries its place.
 */
export function useHourly() {
  const known = useFeaturesKnown();
  const envelopes = useEnvelopeView();
  const q = useApi<any>(known && !envelopes ? "/hourly" : null);
  const client = useQueryClient();
  const asked = useRef(false);
  useEffect(() => {
    if (!q.data || asked.current) return;
    asked.current = true;
    const net = client.getQueryData<any>(["/network"]);
    const lacks = (net?.features ?? []).some((f: any) => f?.properties?.layer === "house" && f.properties.hourly_status == null);
    if (lacks) void client.invalidateQueries({ queryKey: ["/network"] });
  }, [q.data, client]);
  return q;
}
