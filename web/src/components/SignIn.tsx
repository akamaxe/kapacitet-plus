import { useState } from "react";
import { useT } from "../lib/i18n";
import { signIn, takeSignInError, useSignOut, type Me } from "../lib/auth";
import { Roundel } from "./Shell";

/**
 * The page in front of everything when the installation asks for sign-in (KAPACITET_AUTH=oidc)
 * and nobody is signed in, or the person signed in holds no Kapacitet+ role. The bar keeps the
 * product's chrome; the screens and their data stay out of reach until a role is there. The
 * applicant's portal never shows it: it stays open behind its code.
 */
export function SignIn({ me }: { me: Me }) {
  const t = useT();
  const signOut = useSignOut();
  // a sign-in that came back without a session says why, once
  const [failed] = useState(takeSignInError);
  const noRole = !!me.user && me.user.roles.length === 0;
  return (
    <div className="app signin-app">
      <header className="topbar">
        <span className="brand">
          <Roundel />
          <span className="brand-name">Kapacitet+</span>
        </span>
      </header>
      <main className="page signin-page" id="main" tabIndex={-1}>
        <section className="signin" aria-labelledby="signin-title">
          <h1 className="headline" id="signin-title">{t("auth.title")}</h1>
          <p className="headline-sub">{noRole ? t("auth.noRole", { name: me.user!.name }) : t("auth.lead")}</p>
          {failed && !noRole && (
            <p className="signin-error" role="alert">{t(failed === "provider_unavailable" ? "auth.failedProvider" : "auth.failed")}</p>
          )}
          <div className="answer-actions">
            {noRole
              ? <button className="btn" onClick={() => void signOut()}>{t("auth.signOut")}</button>
              : <button className="btn" onClick={signIn}>{t("auth.signIn")}</button>}
          </div>
          <p className="caption">{t("auth.note")}</p>
        </section>
      </main>
    </div>
  );
}
