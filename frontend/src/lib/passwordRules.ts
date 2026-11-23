export const PASSWORD_REQUIREMENT_TEXT = "Password must be 8-128 characters.";

export function getPasswordFieldErrors(password: string, confirmPassword: string) {
  const errors: { password?: string; confirmPassword?: string } = {};
  if (password.length < 8 || password.length > 128) {
    errors.password = "Password does not meet the requirements.";
  }
  if (password !== confirmPassword) {
    errors.confirmPassword = "Passwords do not match.";
  }
  return errors;
}
