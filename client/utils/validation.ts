export const MIN_PASSWORD_LENGTH = 12;

export const PASSWORD_RULES = [
  { key: "length", label: "At least 12 characters", test: (v: string) => v.length >= MIN_PASSWORD_LENGTH },
  { key: "upper", label: "At least one uppercase letter (A-Z)", test: (v: string) => /[A-Z]/.test(v) },
  { key: "lower", label: "At least one lowercase letter (a-z)", test: (v: string) => /[a-z]/.test(v) },
  { key: "number", label: "At least one number (0-9)", test: (v: string) => /\d/.test(v) },
  { key: "symbol", label: "At least one symbol (!@#$%^&*)", test: (v: string) => /[^A-Za-z0-9]/.test(v) },
] as const;

export function checkPasswordList(value: string) {
  return PASSWORD_RULES.map((rule) => ({ ...rule, met: rule.test(value) }));
}

export function passwordStrength(value: string): { score: number; label: string } {
  const met = checkPasswordList(value).filter((rule) => rule.met).length;
  if (met === 0) return { score: 0, label: "Too weak" };
  if (met < 3) return { score: 1, label: "Weak" };
  if (met === 3) return { score: 2, label: "Fair" };
  if (met === 4) return { score: 3, label: "Good" };
  return { score: 4, label: "Strong" };
}

export function validateName(name: string): string {
  const value = String(name || "").trim();
  if (!value) return "Full name is required.";
  if (value.length < 2) return "Full name must be at least 2 characters.";
  if (value.length > 80) return "Full name must be 80 characters or fewer.";
  if (!/^[A-Za-z' .-]+$/.test(value)) {
    return "Name can only contain letters, spaces, hyphens, apostrophes, and periods.";
  }
  if (value.split(/\s+/).length < 2) return "Please enter your first and last name.";
  return "";
}

export function validateEmail(email: string): string {
  const value = String(email || "").trim();
  if (!value) return "Email is required.";
  if (value.length > 254) return "Email is too long.";
  if (/\s/.test(value)) return "Email cannot contain spaces.";
  if (!/^[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}$/.test(value)) {
    return "Enter a valid email address like you@example.com.";
  }
  return "";
}

export function validatePassword(password: string): string {
  const value = String(password || "");
  if (!value) return "Password is required.";
  if (/\s/.test(value)) return "Password cannot contain spaces.";
  const unmet = checkPasswordList(value).find((rule) => !rule.met);
  return unmet ? "Password needs " + unmet.label.charAt(0).toLowerCase() + unmet.label.slice(1) + "." : "";
}

export function validateConfirmPassword(confirmPassword: string, password: string): string {
  if (!String(confirmPassword || "")) return "Please re-enter your password.";
  if (confirmPassword !== password) return "Passwords do not match.";
  return "";
}