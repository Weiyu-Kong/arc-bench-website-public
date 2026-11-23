import { useEffect, useRef, useState } from "react";

import { api } from "../lib/api";
import { normalizeAuthMessage } from "../lib/authErrors";
import { useI18n } from "../i18n/I18nContext";
import type { EmailVerificationChallenge, PasswordResetGrant } from "../lib/types";
import { useAuth } from "./AuthContext";

type Props = {
  challenge: EmailVerificationChallenge;
  onBack: () => void;
} & ({ mode?: "email"; onVerified: () => void } |
     { mode: "password-reset"; onResetVerified: (grant: PasswordResetGrant) => void });

export default function EmailVerificationForm(props: Props) {
  const { t } = useI18n();
  const { challenge: initial, onBack } = props;
  const resetting = props.mode === "password-reset";
  const { verifyEmail } = useAuth();
  const [challenge, setChallenge] = useState(initial);
  const [code, setCode] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const pending = useRef(false);
  const [resendAt, setResendAt] = useState(() => Date.now() + initial.resend_after * 1000);
  const [expiresAt, setExpiresAt] = useState(() => Date.now() + initial.expires_in * 1000);
  const [now, setNow] = useState(Date.now());

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);

  const remaining = Math.max(0, Math.ceil((resendAt - now) / 1000));
  const expired = now >= expiresAt;
  const showError = (cause: unknown) => setError(normalizeAuthMessage(
    cause instanceof Error ? cause.message : "Something went wrong. Please try again later.",
  ));

  const submit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (pending.current) return;
    if (!/^\d{6}$/.test(code.trim())) {
      setError("Enter the six-digit verification code from your email.");
      return;
    }
    pending.current = true;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const payload = { challenge_id: challenge.challenge_id, code: code.trim() };
      if (props.mode === "password-reset") {
        props.onResetVerified(await api.verifyPasswordReset(payload));
      } else {
        await verifyEmail(payload);
        props.onVerified();
      }
    } catch (cause) {
      showError(cause);
    } finally {
      pending.current = false;
      setBusy(false);
    }
  };

  const resend = async () => {
    if (pending.current || remaining > 0) return;
    pending.current = true;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const result = resetting ? await api.resendPasswordReset(challenge.challenge_id)
        : await api.resendVerificationEmail(challenge.challenge_id);
      setChallenge(result);
      setCode("");
      const sentAt = Date.now();
      setNow(sentAt);
      setResendAt(sentAt + result.resend_after * 1000);
      setExpiresAt(sentAt + result.expires_in * 1000);
      setNotice("A new code has been sent. Please use the code from the latest email.");
    } catch (cause) {
      showError(cause);
    } finally {
      pending.current = false;
      setBusy(false);
    }
  };

  return (
    <div className="page auth-page" data-i18n-skip>
      <div className="auth-card">
        <div className="auth-copy">
          <div className="competition-eyebrow">{t("Account")}</div>
          <h1>{t(resetting ? "Reset password: verify email" : "Verify email")}</h1>
          <p>{t("A verification code has been sent to {email}. Please check your inbox and spam folder.")
            .replace("{email}", challenge.email)}</p>
          <p>{t("The code expires in {minutes} minutes.").replace("{minutes}", String(Math.ceil(challenge.expires_in / 60)))}{" "}
            {t(resetting ? "After verification, you can set a new password." : "Verify once to sign in. You will not need to verify again.")}</p>
        </div>
        <form className="auth-form" onSubmit={submit}>
          <label className="field-label" htmlFor="email-verification-code">{t("Email verification code")}</label>
          <input id="email-verification-code" className="text-input" type="text" inputMode="numeric"
            autoComplete="one-time-code" autoFocus maxLength={6} value={code}
            onChange={(event) => { setCode(event.target.value); setError(""); }}
            aria-invalid={Boolean(error)} aria-describedby="email-verification-status" disabled={busy} />
          <div id="email-verification-status" aria-live="polite">
            {error ? <p className="field-error" role="alert">{t(error)}</p> : null}
            {notice ? <p className="field-hint">{t(notice)}</p> : null}
            {expired ? <p className="field-error">{t("The code has expired. Go back to request a new code.")}</p> : null}
          </div>
          <button className="btn-primary" type="submit" disabled={busy || expired}>
            {t(busy ? "Processing..." : resetting ? "Verify and continue" : "Verify and sign in")}
          </button>
          <button className="btn-secondary" type="button" onClick={resend} disabled={busy || remaining > 0 || expired}>
            {remaining > 0 ? t("Resend in {seconds}s").replace("{seconds}", String(remaining)) : t("Resend verification code")}
          </button>
          <button className="btn-secondary" type="button" onClick={onBack} disabled={busy}>{t("Back")}</button>
        </form>
      </div>
    </div>
  );
}
