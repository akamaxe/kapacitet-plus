/**
 * The cable names an export's network information system uses and the program does not know, each
 * given the type it is in the catalogue of Swedish cables (GET /cable-types), at New project's check.
 * The translation goes into the project's cable_types.csv (lib/cableTypes.ts), and the export is
 * checked again with it; what is left untranslated stays an error, or the default cable if chosen.
 */
import { useState } from "react";
import { useApi } from "../lib/api";
import type { CableName, CableType } from "../lib/cableTypes";
import { useFmt } from "../lib/format";
import { useT } from "../lib/i18n";

export function CableNames({ names, chosen, busy, onApply }: {
  /** the names the last check could not translate, with their sections */
  names: CableName[];
  /** the translation made so far: name to catalogue type */
  chosen: Record<string, string>;
  busy: boolean;
  /** the whole new translation; {} takes it back */
  onApply: (next: Record<string, string>) => void;
}) {
  const t = useT();
  const f = useFmt();
  const catalogue = useApi<{ types: CableType[] }>("/cable-types");
  const [pick, setPick] = useState<Record<string, string>>({});
  const types = catalogue.data?.types ?? [];
  const picked = names.filter((x) => pick[x.name]).map((x) => [x.name, pick[x.name]] as const);
  const done = Object.keys(chosen).length;

  return (
    <section className="stack new-cables" aria-labelledby="new-cables">
      <h3 id="new-cables">{t("new.cables.title")}</h3>
      {names.length > 0 && (
        <>
          <p className="caption">{t("new.cables.lead")}</p>
          <table className="data">
            <thead>
              <tr>
                <th scope="col">{t("new.cables.name")}</th>
                <th scope="col" className="r">{t("new.cables.sections")}</th>
                <th scope="col">{t("new.cables.type")}</th>
              </tr>
            </thead>
            <tbody>
              {names.map((x) => (
                <tr key={x.name}>
                  <td>{x.name}</td>
                  <td className="r">{f(x.count)}</td>
                  <td>
                    <select value={pick[x.name] ?? ""} disabled={busy || types.length === 0}
                      aria-label={t("new.cables.typeFor", { name: x.name })}
                      onChange={(e) => { const value = e.currentTarget.value; setPick((p) => ({ ...p, [x.name]: value })); }}>
                      <option value="">{t("new.cables.none")}</option>
                      {types.map((ty) => (
                        <option key={ty.type} value={ty.type}>
                          {t("new.cables.option", { type: ty.type, conductor: ty.conductor, amps: f(Math.round(ty.max_i_ka * 1000)) })}
                        </option>
                      ))}
                    </select>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {catalogue.error && <p className="caption-muted">{t("new.cables.noCatalogue")}</p>}
          <div className="answer-actions">
            <button className="btn" disabled={busy || picked.length === 0}
              onClick={() => { onApply({ ...chosen, ...Object.fromEntries(picked) }); setPick({}); }}>
              {t("new.cables.apply")}
            </button>
          </div>
          <p className="caption-muted">{t("new.cables.source")}</p>
        </>
      )}
      {done > 0 && (
        <p className="new-cables-done">
          <span>{t(done === 1 ? "new.cables.done.one" : "new.cables.done", { n: f(done) })}</span>
          <button className="btn ghost small" disabled={busy} onClick={() => onApply({})}>{t("new.cables.undo")}</button>
        </p>
      )}
    </section>
  );
}
