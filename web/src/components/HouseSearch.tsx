import { useMemo, useState } from "react";
import { useT } from "../lib/i18n";

export interface HouseOption {
  cid: number;
  address: string | null;
}

/**
 * Find a house by its address: the map is one way in, this is the one a keyboard or a screen reader
 * can use. A native input with a list of suggestions; picking an address hands back its house.
 */
export function HouseSearch({ houses, onPick, id = "house-search", label, placeholder }: {
  houses: HouseOption[];
  onPick: (cid: number) => void;
  id?: string;
  label?: string;
  placeholder?: string;
}) {
  const t = useT();
  const [q, setQ] = useState("");
  const byLabel = useMemo(() => {
    const m = new Map<string, number>();
    houses.forEach((h) => m.set(h.address || t("dt.unnamed", { id: h.cid }), h.cid));
    return m;
  }, [houses, t]);
  return (
    <div className="house-search">
      <label htmlFor={id} className="field-label">{label ?? t("req.find")}</label>
      <input
        id={id}
        list={`${id}-list`}
        value={q}
        placeholder={placeholder ?? t("req.findPlaceholder")}
        autoComplete="off"
        onChange={(e) => {
          setQ(e.target.value);
          const cid = byLabel.get(e.target.value);
          if (cid !== undefined) {
            onPick(cid);
            setQ("");
          }
        }}
      />
      <datalist id={`${id}-list`}>
        {[...byLabel.keys()].sort((a, b) => a.localeCompare(b)).map((name) => <option key={name} value={name} />)}
      </datalist>
    </div>
  );
}
