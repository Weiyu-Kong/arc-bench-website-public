import { message } from "antd";
import { useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";

import { useI18n } from "../i18n/I18nContext";
import { useAuth } from "../auth/AuthContext";
import EmailVerificationForm from "../auth/EmailVerificationForm";
import type { EmailVerificationChallenge } from "../lib/types";
import { getLoginErrorMessage } from "../lib/authErrors";

type LoginFieldErrors = {
  email?: string;
  password?: string;
};

export default function LoginPage() {
  const { t } = useI18n();
  const navigate = useNavigate();
  const location = useLocation();
  const { login } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [fieldErrors, setFieldErrors] = useState<LoginFieldErrors>({});
  const [submitting, setSubmitting] = useState(false);
  const [challenge, setChallenge] = useState<EmailVerificationChallenge | null>(null);

  const redirectTo = (location.state as { from?: string } | null)?.from || "/";

  const handleSubmit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const nextErrors: LoginFieldErrors = {};
    if (!email.trim()) {
      nextErrors.email = "Email is required.";
    } else if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.trim())) {
      nextErrors.email = "Please enter a valid email address.";
    }
    if (!password) {
      nextErrors.password = "Password is required.";
    }
    setFieldErrors(nextErrors);
    if (Object.keys(nextErrors).length > 0) {
      return;
    }

    setSubmitting(true);
    try {
      const result = await login({ email, password });
      if ("verification_required" in result) {
        setPassword("");
        setChallenge(result);
        return;
      }
      message.success(t("Signed in successfully."));
      navigate(redirectTo, { replace: true });
    } catch (error) {
      const errorMessage = getLoginErrorMessage(error);
      message.error(t(errorMessage));
      if (errorMessage === "User not found.") {
        setFieldErrors({ email: errorMessage });
      } else if (errorMessage === "Incorrect password.") {
        setFieldErrors({ password: errorMessage });
      } else if (errorMessage === "Email is required.") {
        setFieldErrors({ email: errorMessage });
      } else if (errorMessage === "Password is required.") {
        setFieldErrors({ password: errorMessage });
      }
    } finally {
      setSubmitting(false);
    }
  };

  if (challenge) {
    return <EmailVerificationForm challenge={challenge} onBack={() => setChallenge(null)}
      onVerified={() => { message.success(t("Email verified. You are now signed in.")); navigate(redirectTo, { replace: true }); }} />;
  }

  return (
    <div className="page auth-page" data-i18n-skip>
      <div className="auth-card">
        <div className="auth-copy">
          <div className="competition-eyebrow">{t("Account")}</div>
          <h1>{t("Login")}</h1>
          <p>{t("Sign in with the email and password registered on ARC-Bench.")}</p>
        </div>
        <form className="auth-form" onSubmit={handleSubmit} noValidate>
          <label className="field-label" htmlFor="login-email">
            {t("Email")}
          </label>
          <input
            id="login-email"
            className="text-input"
            type="email"
            autoComplete="email"
            value={email}
            onChange={(event) => {
              setEmail(event.target.value);
              setFieldErrors((current) => ({ ...current, email: undefined }));
            }}
            aria-invalid={Boolean(fieldErrors.email)}
            aria-describedby={fieldErrors.email ? "login-email-error" : undefined}
          />
          {fieldErrors.email ? <p id="login-email-error" className="field-error">{t(fieldErrors.email)}</p> : null}

          <label className="field-label" htmlFor="login-password">
            {t("Password")}
          </label>
          <input
            id="login-password"
            className="text-input"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(event) => {
              setPassword(event.target.value);
              setFieldErrors((current) => ({ ...current, password: undefined }));
            }}
            aria-invalid={Boolean(fieldErrors.password)}
            aria-describedby={fieldErrors.password ? "login-password-error" : undefined}
          />
          {fieldErrors.password ? <p id="login-password-error" className="field-error">{t(fieldErrors.password)}</p> : null}

          <button className="btn-primary" type="submit" disabled={submitting}>
            {t(submitting ? "Signing In..." : "Login")}
          </button>
        </form>
        <div className="auth-footnote">
          <Link className="inline-link" to="/reset-password">{t("Forgot password?")}</Link>
        </div>
        <div className="auth-footnote">
          <span>{t("Need an account?")}</span>
          <Link className="inline-link" to="/register">
            {t("Register")}
          </Link>
        </div>
      </div>
    </div>
  );
}
