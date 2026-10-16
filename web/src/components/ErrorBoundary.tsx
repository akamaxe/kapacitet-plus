/**
 * A screen that throws should cost you that screen, not the application.
 *
 * Without this, any render error empties the page: no sidebar, no theme, no way back.
 * The fallback follows §7 — it names what failed and offers something to do about it.
 */
import { Component, type ErrorInfo, type ReactNode } from "react";
import { Icon } from "./Icon";
import { copyText } from "../lib/clipboard";
import { translate } from "../lib/i18n";
import { useStore } from "../lib/store";

interface Props {
  children: ReactNode;
  /** What the user was looking at, so the message can name it. */
  where: string;
  /** Remount the subtree instead of reloading the page. */
  onReset?: () => void;
}

export class ErrorBoundary extends Component<Props, { error: Error | null }> {
  state = { error: null as Error | null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // eslint-disable-next-line no-console
    console.error("[kapacitet] render failed in", this.props.where, error, info.componentStack);
  }

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;
    const t = (k: string, v?: Record<string, string | number>) => translate(useStore.getState().lang, k, v);
    return (
      <div className="empty" role="alert">
        <div className="loading-orbit">
          <Icon name="alert" size={26} />
        </div>
        <h2>{t("err.title", { where: this.props.where })}</h2>
        <p className="caption">{t("err.body")}</p>
        <p className="caption-muted">{error.message}</p>
        <div className="row">
          <button
            className="btn"
            onClick={() => {
              this.setState({ error: null });
              this.props.onReset?.();
            }}
          >
            {t("err.retry")}
          </button>
          <button
            className="btn ghost"
            onClick={() => { void copyText(`${this.props.where}: ${error.stack ?? error.message}`).catch(() => undefined); }}
          >
            {t("err.copy")}
          </button>
        </div>
      </div>
    );
  }
}
