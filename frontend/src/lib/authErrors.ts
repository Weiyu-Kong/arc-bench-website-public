import { ApiError } from "./api";
import { zhTranslations } from "../i18n/translations";

// Normalize server messages to stable English text. Translate at render time so
// an existing validation error also follows a language switch.
const englishByChinese = new Map(Object.entries(zhTranslations).map(([en, zh]) => [zh, en]));

export function normalizeAuthMessage(value: string): string {
  const attempts = value.match(/^验证码错误，还可以尝试 (\d+) 次。$/);
  if (attempts) return `Incorrect verification code. ${attempts[1]} attempts remaining.`;
  if (value === "Failed to fetch" || value === "NetworkError when attempting to fetch resource.") {
    return "Unable to connect. Please check your network and try again.";
  }
  return englishByChinese.get(value) ?? value;
}

function getErrorText(error: unknown): string {
  if (error instanceof ApiError) {
    return error.message.trim().toLowerCase();
  }
  if (error instanceof Error) {
    return error.message.trim().toLowerCase();
  }
  return "";
}

export function getLoginErrorMessage(error: unknown): string {
  const errorText = getErrorText(error);
  if (error instanceof ApiError && /[\u3400-\u9fff]/.test(error.message)) return normalizeAuthMessage(error.message);

  if (errorText.includes("user not found")) {
    return "User not found.";
  }
  if (errorText.includes("incorrect password")) {
    return "Incorrect password.";
  }
  if (errorText.includes("email is required")) {
    return "Email is required.";
  }
  if (errorText.includes("password is required")) {
    return "Password is required.";
  }
  if (error instanceof ApiError && error.status === 401) {
    return "Login failed. Check your email and password.";
  }

  return "Login failed. Please try again.";
}

export function getRegisterErrorMessage(error: unknown): string {
  const errorText = getErrorText(error);
  if (error instanceof ApiError && /[\u3400-\u9fff]/.test(error.message)) return normalizeAuthMessage(error.message);

  if (errorText.includes("email is already registered")) {
    return "Registration failed. Email already in use.";
  }
  if (errorText.includes("username is already taken")) {
    return "Registration failed. Username is unavailable.";
  }
  if (errorText.includes("between 3 and 32 characters")) {
    return "Registration failed. Username must be 3-32 characters.";
  }
  if (errorText.includes("only contain letters, numbers, underscore, and hyphen")) {
    return "Registration failed. Use letters, numbers, `_` or `-` in the username.";
  }
  if (errorText.includes("password must be at least 8 characters")) {
    return "Registration failed. Password must be at least 8 characters.";
  }
  if (errorText.includes("string should have at least 8 characters")) {
    return "Registration failed. Password must be at least 8 characters.";
  }
  if (errorText.includes("string should have at most 128 characters")) {
    return "Registration failed. Password must be 128 characters or fewer.";
  }
  if (errorText.includes("email must include @")) {
    return "Registration failed. Email must include @.";
  }
  if (errorText.includes("valid email")) {
    return "Registration failed. Enter a valid email.";
  }

  return "Registration failed. Please check your details and try again.";
}
