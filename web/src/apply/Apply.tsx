/**
 * The applicant's portal (#/apply): a household picks its address, says what it wants to connect,
 * reads the answer in plain words, applies, and follows its application by a reference code.
 *
 * It is its own small page: main.tsx loads it instead of the application, so it carries neither the
 * map nor the chart library, and it keeps its own address (`#/apply?ref=KP-…`) rather than the
 * application's. It asks for no personal data. Everything it reads comes from /api/v1/apply.
 */
import { useEffect, useRef, useState } from "react";
import { Mark, Status } from "../components/bits";
import { HouseSearch, type HouseOption } from "../components/HouseSearch";
import { Icon } from "../components/Icon";
import { OwnerFigures } from "../components/OwnerCase";
import { API, useApi } from "../lib/api";
import { refusalOf, refusalText } from "../lib/errors";
import { follow, type Failure } from "../lib/evaluate";
import { dateTime, useFmt } from "../lib/format";
import { LANGS, LANG_NAMES, useT } from "../lib/i18n";
import { describeRequest, fuseText, reasonLine } from "../lib/reasons";
import { useStore } from "../lib/store";

const STAGES = ["static", "envelope", "verify", "risk", "economics"] as const;
const REF = /^KP-[0-9A-F]{4}-[0-9A-F]{4}$/i;

/** The reference in the portal's own address, if any. */
function refInHash(): string | null {
  const q = new URLSearchParams(window.location.hash.split("?")[1] ?? "");
  const ref = (q.get("ref") ?? "").toUpperCase();
  return REF.test(ref) ? ref : null;
}

function writeRef(ref: string | null) {
  history.replaceState(null, "", ref ? `#/apply?ref=${ref}` : "#/apply");
}

/** A key that makes sending the same application twice harmless (a retry, a double click). */
function newKey(): string {
  return Array.from(crypto.getRandomValues(new Uint8Array(12)), (b) => b.toString(16).padStart(2, "0")).join("");
}

export default function Apply() {
  const t = useT();
  const f = useFmt();
  const lang = useStore((s) => s.lang);
  const setLang = useStore((s) => s.setLang);
  const houses = useApi<{ items: HouseOption[]; form: { pv_min_kwp: number; battery_max_kwh: number; ev_kw: number[] } }>("/apply/houses");
  const [cid, setCid] = useState<number | null>(null);
  const [pv, setPv] = useState(10);
  const [ev, setEv] = useState(0);
  const [hp, setHp] = useState(false);
  const [bat, setBat] = useState(0);
  const [stages, setStages] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [answer, setAnswer] = useState<any>(null);
  const [status, setStatus] = useState<any>(null);
  const [ref, setRef] = useState<string>(refInHash() ?? "");
  const key = useRef(newKey());
  const watch = useRef<{ close: () => void } | null>(null);

  useEffect(() => {
    document.title = `${t("apply.title")} · Kapacitet+`;
  }, [t]);
  useEffect(() => () => watch.current?.close(), []);
  // a link with a reference opens on its state
  useEffect(() => {
    const r = refInHash();
    if (r) void lookUp(r);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  // while an application is being decided, its state is read again every few seconds
  useEffect(() => {
    if (status?.status !== "pending") return;
    const id = window.setInterval(() => void lookUp(status.reference, true), 3000);
    return () => window.clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status?.status, status?.reference]);

  const H = houses.data;
  const address = (c: number | null) => H?.items.find((h) => h.cid === c)?.address ?? (c === null ? "" : t("dt.unnamed", { id: c }));
  const nothing = pv <= 0 && ev <= 0 && !hp;
  const failText = (why: Failure, detail?: string) =>
    why === "failed" ? `${t("req.failedPlain")} ${detail ?? ""}`.trim() : why === "timeout" ? t("req.timeout") : t("req.dropped");

  const check = async () => {
    if (cid === null || nothing) return;
    watch.current?.close();
    setError(null);
    setAnswer(null);
    setStatus(null);
    setStages([]);
    setBusy(true);
    key.current = newKey();
    const r = await fetch(`${API}/apply/evaluate`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ cid, pv_kwp: pv, ev_kw: ev, hp, bat_kwh: pv > 0 ? bat : 0 }),
    }).catch(() => null);
    if (!r) {
      setBusy(false);
      return setError(t("ui.offline"));
    }
    if (!r.ok) {
      setBusy(false);
      return setError(refusalText(t, refusalOf(await r.json().catch(() => null)), String(r.status)));
    }
    const { id } = await r.json();
    watch.current = follow(`${API}/apply/evaluate/${id}/events`, `${API}/apply/evaluate/${id}`, STAGES, {
      stage: (s) => setStages((done) => [...done, s]),
      done: (res) => {
        setBusy(false);
        setAnswer(res);
      },
      fail: (why, detail) => {
        setBusy(false);
        setError(failText(why, detail));
      },
    });
  };

  const apply = async () => {
    if (!answer) return;
    setBusy(true);
    setError(null);
    const r = await fetch(`${API}/apply/submit`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ assessment: answer.id, key: key.current }),
    }).catch(() => null);
    setBusy(false);
    if (!r) return setError(t("ui.offline"));
    const body = await r.json().catch(() => null);
    if (!r.ok) return setError(refusalText(t, refusalOf(body), String(r.status)));
    setStatus(body);
    setRef(body.reference);
    writeRef(body.reference);
  };

  async function lookUp(reference: string, quiet = false) {
    const code = reference.trim().toUpperCase();
    if (!REF.test(code)) {
      if (!quiet) setError(t("apply.status.badCode"));
      return;
    }
    const r = await fetch(`${API}/apply/status/${code}`).catch(() => null);
    if (!r) return quiet ? undefined : setError(t("ui.offline"));
    const body = await r.json().catch(() => null);
    if (!r.ok) return quiet ? undefined : setError(refusalText(t, refusalOf(body), String(r.status)));
    if (!quiet) setError(null);
    setStatus(body);
    writeRef(code);
  }

  return (
    <div className="portal">
      <header className="portal-bar">
        <span className="brand">
          <svg className="roundel" width="26" height="26" viewBox="0 0 26 26" aria-hidden="true"><circle cx="13" cy="13" r="13" /><path d="M13 7v12M7 13h12" /></svg>
          <span className="brand-name">Kapacitet+</span>
        </span>
        <div className="top-lang" role="group" aria-label={t("shell.language")}>
          {LANGS.map((l) => (
            <button key={l} className="top-tool" lang={l} aria-pressed={lang === l} title={LANG_NAMES[l]} onClick={() => void setLang(l)}>
              {l.toUpperCase()}
            </button>
          ))}
        </div>
      </header>
      <p className="portal-strip"><Mark kind="real" /> {t("apply.strip.real")} <Mark kind="synthetic" /> {t("apply.strip.synthetic")} {t("apply.strip.private")}</p>

      <main className="portal-main" id="main">
        <h1 className="headline">{t("apply.title")}</h1>
        <p className="headline-sub">{t("apply.lead")}</p>

        {houses.isError ? (
          <p className="caption" role="status"><Status kind="warning">{t("apply.closed")}</Status></p>
        ) : (
          <>
            <section className="portal-step" aria-labelledby="step-house">
              <h2 id="step-house"><span className="portal-n">1</span>{t("apply.step.house")}</h2>
              {H ? (
                <HouseSearch id="apply-house" houses={H.items} onPick={(c) => { setCid(c); setAnswer(null); setStatus(null); }}
                  label={t("apply.house.label")} placeholder={t("req.findPlaceholder")} />
              ) : <p className="caption-muted">{t("apply.loading")}</p>}
              {cid !== null && <p className="portal-chosen"><Icon name="check" size={16} />{address(cid)}</p>}
            </section>

            <section className="portal-step" aria-labelledby="step-what">
              <h2 id="step-what"><span className="portal-n">2</span>{t("apply.step.what")}</h2>
              <div className="portal-form">
                <label className="field-inline">
                  <span>{t("apply.pv")}</span>
                  <input type="number" min={0} max={40} step={0.5} value={pv} onChange={(e) => setPv(Math.max(0, Number(e.target.value) || 0))} />
                  <span>kWp</span>
                </label>
                <label className="field-inline">
                  <span>{t("apply.ev")}</span>
                  <select value={ev} onChange={(e) => setEv(Number(e.target.value))}>
                    <option value={0}>{t("apply.none")}</option>
                    {(H?.form.ev_kw ?? []).map((kw) => <option key={kw} value={kw}>{f(kw, kw % 1 ? 1 : 0)} kW</option>)}
                  </select>
                </label>
                <label className="field-inline">
                  <input type="checkbox" checked={hp} onChange={(e) => setHp(e.target.checked)} />
                  <span>{t("apply.hp")}</span>
                </label>
                <label className="field-inline">
                  <span>{t("apply.battery")}</span>
                  <input type="number" min={0} max={H?.form.battery_max_kwh ?? 0} step={1} value={bat} disabled={pv <= 0}
                    onChange={(e) => setBat(Math.max(0, Math.min(H?.form.battery_max_kwh ?? 0, Number(e.target.value) || 0)))} />
                  <span>kWh</span>
                </label>
              </div>
              {H && pv > 0 && pv < H.form.pv_min_kwp && <p className="caption-muted">{t("apply.pvMin", { kwp: f(H.form.pv_min_kwp, 1) })}</p>}
            </section>

            <section className="portal-step" aria-labelledby="step-answer">
              <h2 id="step-answer"><span className="portal-n">3</span>{t("apply.step.answer")}</h2>
              <button className="btn" disabled={busy || cid === null || nothing} onClick={check}>
                {busy && !answer ? t("apply.checking") : t("apply.check")}
              </button>
              {busy && !answer && (
                <p className="caption-muted" role="status">{t("apply.progress", { done: f(stages.length), of: f(STAGES.length) })}</p>
              )}
              {answer && <Answer a={answer} />}
              {answer && !status && (
                <div className="portal-apply">
                  <button className="btn" disabled={busy} onClick={apply}>{t("apply.submit")}</button>
                  <p className="caption-muted">{t("apply.submitNote")}</p>
                </div>
              )}
            </section>
          </>
        )}

        {status && <StatusCard s={status} />}

        <section className="portal-step" aria-labelledby="step-status">
          <h2 id="step-status">{t("apply.status.title")}</h2>
          <form className="portal-form" onSubmit={(e) => { e.preventDefault(); void lookUp(ref); }}>
            <label className="field-inline">
              <span>{t("apply.status.code")}</span>
              <input value={ref} onChange={(e) => setRef(e.target.value)} placeholder="KP-0000-0000" autoComplete="off" spellCheck={false} />
            </label>
            <button className="btn ghost" type="submit">{t("apply.status.go")}</button>
          </form>
        </section>
        {error && <p className="decision-error" role="alert"><Icon name="alert" size={15} />{error}</p>}
      </main>
    </div>
  );
}

/** The answer in the words a household needs. */
function Answer({ a }: { a: any }) {
  const t = useT();
  const f = useFmt();
  const e = a.envelope ?? {};
  return (
    <div className="portal-answer stack" aria-live="polite">
      <p className="lead-answer">
        {a.firm
          ? t("apply.ans.firm")
          : t("apply.ans.envelope", { mean: f(e.curtailment_mean_pct, 1), p90: f(e.curtailment_p90_pct, 1) })}
      </p>
      <ul className="portal-points">
        {!a.firm && e.hours_limited_ref != null && <li>{t("apply.ans.hours", { h: f(e.hours_limited_ref) })}</li>}
        {e.ev_unmet_pct != null && e.ev_unmet_pct > 0 && <li>{t("apply.ans.ev", { pct: f(e.ev_unmet_pct, 1) })}</li>}
        {a.fuse && <li>{fuseText(t, f, a.fuse)}</li>}
        <li>{t(`apply.rule.${a.rule.auto ? a.rule.outcome : "manual"}`)}</li>
      </ul>
      {a.owner && !a.firm && <OwnerFigures O={a.owner} headingId="apply-owner" />}
      <p className="caption-muted">{t(`apply.base.${a.baseline === "queue" ? "queue" : "published"}`)}</p>
    </div>
  );
}

/** Where an application stands, read with its reference code. */
function StatusCard({ s }: { s: any }) {
  const t = useT();
  const f = useFmt();
  const lang = useStore((st) => st.lang);
  const kind = s.status === "firm" || s.status === "nonfirm" ? "good" : s.status === "declined" ? "critical" : "warning";
  return (
    <section className="portal-status" aria-labelledby="status-title" aria-live="polite">
      <h2 id="status-title">{t("apply.status.yours")}</h2>
      <p className="portal-ref">{s.reference}</p>
      <p>{s.status === "pending" ? <span className="status measured">{t("inbox.status.pending")}</span>
        : <Status kind={kind}>{t(`inbox.status.${s.status}`)}</Status>}
        {s.by && <small> · {t(`inbox.by.${s.by}`)}</small>}</p>
      <p className="caption">{s.address} · {describeRequest(t, f, s.request ?? {})}</p>
      <p className="caption-muted">{t("apply.status.received", { when: dateTime(lang, s.received) })}
        {s.decided ? ` · ${t("apply.status.decided", { when: dateTime(lang, s.decided) })}` : ""}</p>
      {s.status === "review" && s.reasons?.length > 0 && (
        <ul className="inbox-reasons">{s.reasons.map((r: any, i: number) => <li key={i}>{reasonLine(t, f, r)}</li>)}</ul>
      )}
      <p className="caption-muted">{t("apply.status.keep")}</p>
    </section>
  );
}
