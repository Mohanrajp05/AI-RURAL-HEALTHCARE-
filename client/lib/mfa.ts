/**
 * MFA (multi-factor authentication) helpers built entirely on the built-in
 * TOTP support of @supabase/supabase-js (same standard used by Google
 * Authenticator / Authy / Microsoft Authenticator).
 *
 * This module exists for three reasons:
 *  1. Keep the MFA session logic (AAL levels, factor listing, challenges)
 *     in one place so route guards, login, and the settings page behave
 *     identically.
 *  2. Generate + burn client-side "recovery codes". Supabase does NOT ship
 *     backup codes natively, so we keep only SHA-256 hashes of the codes in
 *     the user's `user_metadata.recovery_codes`, so plain-text codes never
 *     touch the server.
 *  3. Track a recovery-code elevation *in memory only* (never localStorage)
 *     because Supabase has no native recovery-code -> aal2 step.
 */

import { supabase } from "@/lib/supabaseClient";

export const RECOVERY_CODES_METADATA_KEY = "recovery_codes";
export const RECOVERY_CODE_COUNT = 8;
export const RECOVERY_CODE_PATTERN = /^[A-Z]{4}-\d{4}$/;

const LETTERS = "ABCDEFGHJKLMNPQRSTUVWXYZ"; // no I/O to avoid typo-confusion
const DIGITS = "0123456789";

export interface VerifiedFactor {
  id: string;
  type: string;
}

export type VerifyResult = { ok: boolean; error?: string; rateLimited?: boolean };

/**
 * Lists only the factors that have been fully verified (activated) for the
 * current session's user. Unverified factors (e.g. an abandoned enrolment)
 * are ignored.
 */
export async function listVerifiedFactors(): Promise<VerifiedFactor[]> {
  const { data, error } = await supabase.auth.mfa.listFactors();
  if (error) return [];
  return (data?.all ?? [])
    .filter((factor) => factor.status === "verified")
    .map((factor) => ({ id: factor.id, type: factor.factor_type }));
}

/**
 * Returns the current Authenticator Assurance Level of the session, or null
 * when there is no active session.
 */
export async function getCurrentAal(): Promise<"aal1" | "aal2" | null> {
  const { data } = await supabase.auth.mfa.getAuthenticatorAssuranceLevel();
  return (data?.currentLevel ?? null) as "aal1" | "aal2" | null;
}

/**
 * In-memory flag set when a user logs back in with a recovery code.
 *
 * LIMITATION (documented as requested): Supabase has no native way to
 * elevate a session from aal1 to aal2 with a recovery code, so after a
 * successful recovery-code login we only remember the verification for the
 * lifetime of this browser tab. A page reload returns the user to aal1 and
 * they must use their authenticator. Recovery codes are one-time-use and are
 * burned on success.
 */
let recoveryActive = false;
/** Kept in memory only — never stored, cleared on sign out and page reload. */
export function markRecoveryActive(active: boolean) {
  recoveryActive = active;
}
export function isRecoveryActive() {
  return recoveryActive;
}

/**
 * True when the current session must be stepped up to aal2 before the user
 * may continue: the user is signed in at aal1 and has at least one verified
 * TOTP factor enrolled. False when aal2 is already reached, no factor is
 * enrolled, or no session exists.
 */
export async function mfaChallengeRequired(): Promise<boolean> {
  if (isRecoveryActive()) return false;
  const aal = await getCurrentAal();
  if (aal === "aal2") return false;
  if (aal === null) return false;
  const factors = await listVerifiedFactors();
  return factors.length > 0;
}

function rateLimited(error: { status?: number; statusCode?: number; code?: string; message?: string }): boolean {
  if (typeof error?.status === "number" && error.status === 429) return true;
  if (typeof error?.statusCode === "number" && error.statusCode === 429) return true;
  const message = `${error?.message ?? ""} ${error?.code ?? ""}`.toLowerCase();
  return message.includes("rate limit") || message.includes("too many");
}

/**
 * Attempts a challenge-and-verify against an enrolled factor, retrying once
 * with a freshly issued challenge if the first attempt raced the challenge
 * expiry window. Used by the /mfa-challenge page, the settings enrolment
 * step, and the unenrol flow.
 */
export async function verifyTotpFactor(factorId: string, code: string): Promise<VerifyResult> {
  const first = await supabase.auth.mfa.challengeAndVerify({ factorId, code });
  if (!first.error) return { ok: true };
  if (rateLimited(first.error)) return { ok: false, rateLimited: true, error: first.error.message };

  const message = first.error.message?.toLowerCase() ?? "";
  if (message.includes("expired") || message.includes("challenge")) {
    const { data: challenge, error: challengeError } = await supabase.auth.mfa.challenge({ factorId });
    if (challengeError) {
      return rateLimited(challengeError)
        ? { ok: false, rateLimited: true, error: challengeError.message }
        : { ok: false, error: challengeError.message };
    }
    const { error: verifyError } = await supabase.auth.mfa.verify({ factorId, challengeId: challenge.id, code });
    if (!verifyError) return { ok: true };
    if (rateLimited(verifyError)) return { ok: false, rateLimited: true, error: verifyError.message };
  }

  return { ok: false, error: first.error.message || "Incorrect code. Please try again." };
}

/* ----------------------------- recovery codes ----------------------------- */

function randomIndex(max: number): number {
  const buf = new Uint32Array(1);
  if (typeof crypto !== "undefined" && crypto.getRandomValues) crypto.getRandomValues(buf);
  return buf[0] % max;
}

function randomString(length: number, alphabet: string): string {
  let out = "";
  for (let i = 0; i < length; i++) out += alphabet[randomIndex(alphabet.length)];
  return out;
}

/** One recovery code, e.g. "XKQT-7291" (4 letters, dash, 4 digits). */
export function generateRecoveryCode(): string {
  return `${randomString(4, LETTERS)}-${randomString(4, DIGITS)}`;
}

export function generateRecoveryCodes(count = RECOVERY_CODE_COUNT): string[] {
  const codes: string[] = [];
  for (let i = 0; i < count; i++) codes.push(generateRecoveryCode());
  return codes;
}

/** SHA-256 hex digest via the Web Crypto API (requires a secure context). */
export async function sha256(input: string): Promise<string> {
  if (typeof crypto === "undefined" || !crypto.subtle) {
    throw new Error("Recovery codes require a secure connection (https or localhost).");
  }
  const data = new TextEncoder().encode(input);
  const digest = await crypto.subtle.digest("SHA-256", data);
  return Array.from(new Uint8Array(digest))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

export async function getRecoveryCodeHashes(): Promise<string[]> {
  const { data } = await supabase.auth.getUser();
  const hashes = data.user?.user_metadata?.[RECOVERY_CODES_METADATA_KEY];
  return Array.isArray(hashes) ? (hashes as string[]) : [];
}

async function setRecoveryCodeHashes(hashes: string[]): Promise<string | null> {
  const { error } = await supabase.auth.updateUser({
    data: { [RECOVERY_CODES_METADATA_KEY]: hashes },
  });
  return error?.message ?? null;
}

export async function storeRecoveryCodes(codes: string[]): Promise<string | null> {
  const hashes = await Promise.all(codes.map((code) => sha256(code)));
  return setRecoveryCodeHashes(hashes);
}

/** Removes every stored recovery-code hash (called when MFA is disabled). */
export async function clearRecoveryCodes(): Promise<string | null> {
  return setRecoveryCodeHashes([]);
}

/**
 * Validates a submitted recovery code. On success the code is burned (its
 * hash is removed from the user's metadata) and the in-memory recovery flag
 * is set so the route guard lets the user through for this tab session.
 */
export async function consumeRecoveryCode(rawCode: string): Promise<{ valid: boolean; error?: string }> {
  const code = rawCode.trim().toUpperCase().replace(/\s+/g, "");
  if (!code) return { valid: false };
  const hashes = await getRecoveryCodeHashes();
  if (hashes.length === 0) return { valid: false };

  const digest = await sha256(code);
  if (!hashes.includes(digest)) return { valid: false };

  const remaining = hashes.filter((hash) => hash !== digest);
  const error = await setRecoveryCodeHashes(remaining);
  if (error) return { valid: false, error };

  markRecoveryActive(true);
  return { valid: true };
}