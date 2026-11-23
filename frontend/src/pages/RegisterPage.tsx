import { message } from "antd";
import { useRef, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";

import { useI18n } from "../i18n/I18nContext";
import { useAuth } from "../auth/AuthContext";
import EmailVerificationForm from "../auth/EmailVerificationForm";
import type { EmailVerificationChallenge } from "../lib/types";
import { getRegisterErrorMessage } from "../lib/authErrors";
import { getPasswordFieldErrors, PASSWORD_REQUIREMENT_TEXT } from "../lib/passwordRules";

const USERNAME_REQUIREMENT_TEXT = "Username must be 3-32 characters and use letters, numbers, `_`, or `-`.";

type RegisterFieldErrors = {
  email?: string;
  username?: string;
  password?: string;
  confirmPassword?: string;
};

export default function RegisterPage() {
  const { t } = useI18n();
  const navigate = useNavigate();
  const location = useLocation();
  const { register } = useAuth();
  const [email, setEmail] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [fieldErrors, setFieldErrors] = useState<RegisterFieldErrors>({});
  const [submitting, setSubmitting] = useState(false);
  const [challenge, setChallenge] = useState<EmailVerificationChallenge | null>(null);
  const submittingRef = useRef(false);

  const redirectTo = (location.state as { from?: string } | null)?.from || "/";

  const handleSubmit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (submittingRef.current) return;
    const nextErrors: RegisterFieldErrors = getPasswordFieldErrors(password, confirmPassword);
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.trim())) {
      nextErrors.email = "Please enter a valid email address.";
    }
    if (!username.trim()) {
      nextErrors.username = "Username is required.";
    }
    setFieldErrors(nextErrors);
    if (Object.keys(nextErrors).length > 0) {
      return;
    }

    submittingRef.current = true;
    setSubmitting(true);
    try {
      const result = await register({
        email,
        username,
        password,
      });
      if ("verification_required" in result) {
        setPassword("");
        setConfirmPassword("");
        setChallenge(result);
        return;
      }
      message.success(t("Account created successfully."));
      navigate(redirectTo, { replace: true });
    } catch (error) {
      const errorMessage = getRegisterErrorMessage(error);
      message.error(t(errorMessage));
      const lower = errorMessage.toLowerCase();
      if (lower.includes("email")) {
        setFieldErrors({ email: errorMessage });
      } else if (lower.includes("username")) {
        setFieldErrors({ username: errorMessage });
      } else if (lower.includes("password")) {
        setFieldErrors({ password: errorMessage });
      }
    } finally {
      submittingRef.current = false;
      setSubmitting(false);
    }
  };

  if (challenge) {
    return <EmailVerificationForm challenge={challenge} onBack={() => setChallenge(null)}
      onVerified={() => { message.success(t("Email verified. Your account has been created and you are now signed in.")); navigate(redirectTo, { replace: true }); }} />;
  }

  return (
    <div className="page auth-page" data-i18n-skip>
      <div className="auth-card">
        <div className="auth-copy">
          <div className="competition-eyebrow">{t("Account")}</div>
          <h1>{t("Register")}</h1>
          <p>{t("Create an account to keep your submissions private and attached to your own history.")}</p>
        </div>
        <form className="auth-form" onSubmit={handleSubmit} noValidate>
          <label className="field-label" htmlFor="register-email">
            {t("Email")}
          </label>
          <input
            id="register-email"
            className="text-input"
            type="email"
            autoComplete="email"
            value={email}
            placeholder="you@example.com"
            onChange={(event) => {
              setEmail(event.target.value);
              setFieldErrors((current) => ({ ...current, email: undefined }));
            }}
            aria-invalid={Boolean(fieldErrors.email)}
            aria-describedby={fieldErrors.email ? "register-email-error" : undefined}
          />
          {fieldErrors.email ? <p id="register-email-error" className="field-error">{t(fieldErrors.email)}</p> : null}

          <label className="field-label" htmlFor="register-username">
            {t("Username")}
          </label>
          <input
            id="register-username"
            className="text-input"
            type="text"
            autoComplete="username"
            value={username}
            placeholder={t("letters, numbers, _ or -")}
            onChange={(event) => {
              setUsername(event.target.value);
              setFieldErrors((current) => ({ ...current, username: undefined }));
            }}
            aria-invalid={Boolean(fieldErrors.username)}
            aria-describedby={fieldErrors.username ? "register-username-error" : "register-username-hint"}
          />
          {fieldErrors.username ? <p id="register-username-error" className="field-error">{t(fieldErrors.username)}</p> : <p id="register-username-hint" className="field-hint">{t(USERNAME_REQUIREMENT_TEXT)}</p>}

          <label className="field-label" htmlFor="register-password">
            {t("Password")}
          </label>
          <input
            id="register-password"
            className="text-input"
            type="password"
            autoComplete="new-password"
            minLength={8}
            maxLength={128}
            value={password}
            placeholder={t("8-128 characters")}
            onChange={(event) => {
              setPassword(event.target.value);
              setFieldErrors((current) => ({ ...current, password: undefined }));
            }}
            aria-invalid={Boolean(fieldErrors.password)}
            aria-describedby={fieldErrors.password ? "register-password-error" : "register-password-hint"}
          />
          {fieldErrors.password ? <p id="register-password-error" className="field-error">{t(fieldErrors.password)}</p> : <p id="register-password-hint" className="field-hint">{t(PASSWORD_REQUIREMENT_TEXT)}</p>}

          <label className="field-label" htmlFor="register-confirm-password">
            {t("Confirm password")}
          </label>
          <input
            id="register-confirm-password"
            className="text-input"
            type="password"
            autoComplete="new-password"
            minLength={8}
            maxLength={128}
            value={confirmPassword}
            placeholder={t("Enter the password again")}
            onChange={(event) => {
              setConfirmPassword(event.target.value);
              setFieldErrors((current) => ({ ...current, confirmPassword: undefined }));
            }}
            aria-invalid={Boolean(fieldErrors.confirmPassword)}
            aria-describedby={fieldErrors.confirmPassword ? "register-confirm-password-error" : undefined}
          />
          {fieldErrors.confirmPassword ? <p id="register-confirm-password-error" className="field-error">{t(fieldErrors.confirmPassword)}</p> : null}

          <button className="btn-primary" type="submit" disabled={submitting}>
            {t(submitting ? "Creating..." : "Register")}
          </button>
        </form>
        <div className="auth-footnote">
          <span>{t("Already have an account?")}</span>
          <Link className="inline-link" to="/login">
            {t("Login")}
          </Link>
        </div>
      </div>
    </div>
  );
}
