/**
 * The data-quality report on a network (POST /check): whether it can be used, how complete it is,
 * and each finding as a mark with its word and the engine's sentence in the reader's language.
 * The method screen shows it for a network someone only wants checked; the new-project steps show
 * it before a project is made.
 */
import { Status } from "./bits";
import { useFmt } from "../lib/format";
import { useT } from "../lib/i18n";

export interface Finding {
  severity: string;
  check: string;
  message: string;
  count?: number;
  code?: string;
  params?: Record<string, string | number>;
}

export interface Report {
  usable: boolean;
  score: number;
  tables?: Record<string, number>;
  findings: Finding[];
}

const SEVERITY: Record<string, "critical" | "warning"> = { error: "critical", warning: "warning" };

export function CheckReport({ report }: { report: Report }) {
  const t = useT();
  const f = useFmt();
  // a check this version has no words for keeps the engine's own name
  const checkLabel = (check: string) => {
    const key = `chk.${check.replace(/ /g, "_")}`;
    const label = t(key);
    return label === key ? check : label;
  };
  // the engine's sentence, said in the reader's language when this version knows its code
  const findingText = (x: Finding) => {
    if (!x.code) return x.message;
    const key = `chk.msg.${x.code}`;
    const text = t(key, x.params);
    return text === key ? x.message : text;
  };
  return (
    <div className="stack" aria-live="polite">
      <div className="netcheck-verdict">
        {report.usable ? <Status kind="good">{t("met.imp.usable")}</Status> : <Status kind="critical">{t("met.imp.unusable")}</Status>}
        <span className="netcheck-score">{t("met.imp.score", { s: f(report.score) })}</span>
      </div>
      <p className="caption-muted">{Object.entries(report.tables ?? {}).map(([k, v]) => `${k}: ${f(v)}`).join(" · ")}</p>
      {report.findings.length === 0 ? (
        <p className="caption">{t("met.imp.clean")}</p>
      ) : (
        <ul className="netcheck-findings">
          {report.findings.map((x, i) => (
            <li key={i}>
              {SEVERITY[x.severity]
                ? <Status kind={SEVERITY[x.severity]}>{t(`met.imp.sev.${x.severity}`)}</Status>
                : <span className="status measured">{t(`met.imp.sev.${x.severity}`)}</span>}
              <span><strong>{checkLabel(x.check)}</strong>{x.count ? ` (${f(x.count)})` : ""} — {findingText(x)}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
