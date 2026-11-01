import { useQueryClient } from "@tanstack/react-query";
import { CommandLine } from "./bits";
import { Icon } from "./Icon";
import { useT } from "../lib/i18n";

/** The browser is usually on the machine that runs the server, so its system picks the command. */
const ACTIVATE = typeof navigator !== "undefined" && /Win/.test(navigator.userAgent)
  ? ".venv\\Scripts\\activate"
  : "source .venv/bin/activate";

/**
 * What a fresh clone shows instead of empty screens: the interface reads what the pipeline
 * computed, and on this machine it has not run yet. The exact commands, in order, and what is
 * missing, from /health.
 */
export function Setup({ health }: { health: any }) {
  const t = useT();
  const client = useQueryClient();
  const parts = Object.entries(health.parts ?? {}) as [string, any][];
  const missing = parts.filter(([, p]) => !p.ready);
  return (
    <div className="page">
      <div className="page-inner setup">
        <h1 className="headline">{t("setup.title")}</h1>
        <p className="headline-sub">{t("setup.lead")}</p>
        <ol className="setup-steps">
          <li>
            <p>{t("setup.env")}</p>
            <CommandLine command={ACTIVATE} />
          </li>
          <li>
            <p>{t("setup.all")}</p>
            <CommandLine command="python -m kapacitet all" />
          </li>
          <li>
            <p>{t("setup.bundle")}</p>
            <CommandLine command="python -m kapacitet unbundle kapacitet-data.zip" />
          </li>
          <li>
            <p>{t("setup.serve")}</p>
            <CommandLine command="python -m kapacitet serve" />
          </li>
        </ol>
        <section className="block stack">
          <div className="block-head"><h3>{t("setup.missing")}</h3></div>
          <ul className="list caption">
            {missing.map(([part, p]) => (
              <li key={part}><strong>{t(`setup.part.${part}`)}</strong> · {p.missing.join(", ")}{p.command ? <> · <code>{p.command}</code></> : null}</li>
            ))}
          </ul>
        </section>
        <div>
          <button className="btn" onClick={() => void client.invalidateQueries()}>
            <Icon name="reset" size={16} />{t("setup.again")}
          </button>
        </div>
      </div>
    </div>
  );
}
