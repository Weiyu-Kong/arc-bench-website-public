import { message } from "antd";
import { useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import EmailVerificationForm from "../auth/EmailVerificationForm";
import { useAuth } from "../auth/AuthContext";
import { api } from "../lib/api";
import { normalizeAuthMessage } from "../lib/authErrors";
import { useI18n } from "../i18n/I18nContext";
import { getPasswordFieldErrors, PASSWORD_REQUIREMENT_TEXT } from "../lib/passwordRules";
import type { EmailVerificationChallenge, PasswordResetGrant } from "../lib/types";

export default function ResetPasswordPage() {
  const { t } = useI18n();
  const navigate = useNavigate();
  const { refresh } = useAuth();
  const [email, setEmail] = useState("");
  const [challenge, setChallenge] = useState<EmailVerificationChallenge | null>(null);
  const [grant, setGrant] = useState<(PasswordResetGrant & { expiresAt: number }) | null>(null);
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [fieldErrors, setFieldErrors] = useState<{ password?: string; confirmPassword?: string }>({});
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const pending = useRef(false);

  const restart = () => {
    setChallenge(null);
    setGrant(null);
    setPassword("");
    setConfirmPassword("");
    setFieldErrors({});
    setError("");
  };

  const submit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (pending.current) return;
    setError("");
    if (grant) {
      const errors = getPasswordFieldErrors(password, confirmPassword);
      setFieldErrors(errors);
      if (Object.keys(errors).length) return;
      if (Date.now() >= grant.expiresAt) {
        setError("Your reset authorization has expired. Request a new verification code.");
        return;
      }
    } else if (!email.trim()) {
      setError("Enter your registered email address.");
      return;
    } else if (!grant && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.trim())) {
      setError("Please enter a valid email address.");
      return;
    }
    pending.current = true;
    setBusy(true);
    try {
      if (grant) {
        await api.completePasswordReset({ reset_token: grant.reset_token, password, confirm_password: confirmPassword });
        setPassword("");
        setConfirmPassword("");
        setGrant(null);
        await refresh();
        message.success(t("Password reset successfully. Sign in with your new password."));
        navigate("/login", { replace: true });
      } else {
        setChallenge(await api.requestPasswordReset(email.trim()));
      }
    } catch (cause) {
      const text = normalizeAuthMessage(cause instanceof Error ? cause.message : "Something went wrong. Please try again later.");
      if (text.includes("Passwords do not match")) {
        setFieldErrors({ confirmPassword: "Passwords do not match." });
      } else if (text.includes("Password must")) {
        setFieldErrors({ password: PASSWORD_REQUIREMENT_TEXT });
      } else {
        setError(text);
      }
    } finally {
      pending.current = false;
      setBusy(false);
    }
  };

  if (challenge && !grant) {
    return <EmailVerificationForm mode="password-reset" challenge={challenge} onBack={restart}
      onResetVerified={(result) => { setError(""); setGrant({ ...result, expiresAt: Date.now() + result.expires_in * 1000 }); }} />;
  }

  return (
    <div className="page auth-page" data-i18n-skip>
      <div className="auth-card">
        <div className="auth-copy">
          <div className="competition-eyebrow">{t("Account")}</div>
          <h1>{t("Reset password")}</h1>
          <p>{t(grant ? "Email verified. Enter and confirm your new password." : "Enter your registered email address to receive a verification code.")}</p>
        </div>
        <form className="auth-form" onSubmit={submit} noValidate>
          {!grant ? <>
            <label className="field-label" htmlFor="reset-email">{t("Email")}</label>
            <input id="reset-email" className="text-input" type="email" autoComplete="email" value={email}
              onChange={(event) => { setEmail(event.target.value); setError(""); }} disabled={busy}
              aria-invalid={Boolean(error)} aria-describedby="reset-password-status" />
          </> : <>
            <label className="field-label" htmlFor="reset-new-password">{t("New password")}</label>
            <input id="reset-new-password" className="text-input" type="password" autoComplete="new-password"
              value={password} disabled={busy} onChange={(event) => {
                setPassword(event.target.value); setFieldErrors((current) => ({ ...current, password: undefined }));
              }} aria-invalid={Boolean(fieldErrors.password)} aria-describedby="reset-password-hint reset-password-error" />
            <p id="reset-password-hint" className="field-hint">{t(PASSWORD_REQUIREMENT_TEXT)}</p>
            {fieldErrors.password ? <p id="reset-password-error" className="field-error" role="alert">{t(fieldErrors.password)}</p> : null}
            <label className="field-label" htmlFor="reset-confirm-password">{t("Confirm new password")}</label>
            <input id="reset-confirm-password" className="text-input" type="password" autoComplete="new-password"
              value={confirmPassword} disabled={busy} onChange={(event) => {
                setConfirmPassword(event.target.value); setFieldErrors((current) => ({ ...current, confirmPassword: undefined }));
              }} aria-invalid={Boolean(fieldErrors.confirmPassword)} aria-describedby="reset-confirm-error" />
            {fieldErrors.confirmPassword ? <p id="reset-confirm-error" className="field-error" role="alert">{t(fieldErrors.confirmPassword)}</p> : null}
          </>}
          <div id="reset-password-status" aria-live="polite">
            {error ? <p className="field-error" role="alert">{t(error)}</p> : null}
          </div>
          <button className="btn-primary" type="submit" disabled={busy}>
            {t(busy ? "Processing..." : grant ? "Confirm password reset" : "Send verification code")}
          </button>
          {grant ? <button className="btn-secondary" type="button" onClick={restart} disabled={busy}>{t("Verify email again")}</button> : null}
        </form>
        <div className="auth-footnote"><Link className="inline-link" to="/login">{t("Back to login")}</Link></div>
      </div>
    </div>
  );
}
