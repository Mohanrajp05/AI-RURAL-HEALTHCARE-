/**
 * "Security" card embedded in the profile/settings page. Handles the whole
 * MFA lifecycle for the current user:
 *
 *   - shows current status (enabled / not enabled)
 *   - "Set up authenticator app" - Google-style 3-step enrolment
 *     (enroll -> show QR + secret -> verify 6-digit code -> recovery codes)
 *   - "Remove authenticator" - confirmation modal that first requires the
 *     current OTP code (same requirement GitHub enforces before disabling 2FA)
 *
 * All MFA state stays in this component; the only thing persisted on the
 * user record is the SHA-256 hashes of the one-time recovery codes.
 */

import { useState, useEffect, useCallback } from "react";
import { useAuth } from "@/context/AuthContext";
import {
  ShieldCheck,
  ShieldOff,
  Loader2,
  Copy,
  Download,
  Check,
  Smartphone,
  AlertTriangle,
} from "lucide-react";
import OtpInput from "@/components/mfa/OtpInput";
import {
  listVerifiedFactors,
  verifyTotpFactor,
  generateRecoveryCodes,
  storeRecoveryCodes,
  clearRecoveryCodes,
  type VerifiedFactor,
} from "@/lib/mfa";
import { supabase } from "@/lib/supabaseClient";

interface Enrolment {
  factorId: string;
  qrCode: string;
  secret: string;
}

/** Inline recovery-codes modal shown exactly once right after activation. */
function RecoveryCodesModal({ codes, onDone }: { codes: string[]; onDone: () => void }) {
  const [saved, setSaved] = useState(false);
  const [copied, setCopied] = useState(false);

  const copyAll = async () => {
    try {
      await navigator.clipboard.writeText(codes.join("\n"));
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      setCopied(false);
    }
  };

  const downloadCodes = () => {
    const blob = new Blob([`Rural Healthcare - recovery codes\nGenerated: ${new Date().toISOString()}\n\n${codes.join("\n")}\n\nEach code can only be used once.\n`], {
      type: "text/plain",
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "rural-healthcare-recovery-codes.txt";
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
      <div className="bg-white rounded-2xl shadow-2xl w-full max-w-md overflow-hidden">
        <div className="bg-gradient-to-r from-primary to-accent px-6 py-4 text-white text-center">
          <h3 className="text-lg font-bold">Backup recovery codes (8)</h3>
          <p className="text-white/80 text-xs mt-1">For when you lose access to your authenticator app</p>
        </div>
        <div className="p-6">
          <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 mb-4">
            <p className="text-xs text-amber-800 leading-relaxed">
              <AlertTriangle className="inline w-3.5 h-3.5 mr-1 -mt-0.5" />
              Save these codes somewhere safe. Each code can only be used once. You will not be able to see them again.
            </p>
          </div>

          <div className="grid grid-cols-2 gap-2 mb-4">
            {codes.map((code) => (
              <div key={code} className="px-3 py-2 bg-gray-50 border border-border rounded-lg">
                <span className="font-mono text-sm text-foreground tracking-wider">{code}</span>
              </div>
            ))}
          </div>

          <div className="flex gap-2 mb-4">
            <button
              type="button"
              onClick={downloadCodes}
              className="flex-1 flex items-center justify-center gap-1.5 px-3 py-2 border border-border rounded-lg text-sm font-medium text-foreground hover:bg-gray-50 transition"
            >
              <Download className="w-4 h-4" />
              Download
            </button>
            <button
              type="button"
              onClick={copyAll}
              className="flex-1 flex items-center justify-center gap-1.5 px-3 py-2 border border-border rounded-lg text-sm font-medium text-foreground hover:bg-gray-50 transition"
            >
              {copied ? <Check className="w-4 h-4 text-green-600" /> : <Copy className="w-4 h-4" />}
              {copied ? "Copied!" : "Copy all"}
            </button>
          </div>

          <label className="flex items-start gap-2.5 mb-4 cursor-pointer">
            <input
              type="checkbox"
              checked={saved}
              onChange={(e) => setSaved(e.target.checked)}
              className="mt-0.5 w-4 h-4 accent-primary"
            />
            <span className="text-sm text-muted-foreground">I have saved my codes</span>
          </label>

          <button
            type="button"
            disabled={!saved}
            onClick={onDone}
            className="w-full py-2.5 bg-primary text-primary-foreground font-semibold rounded-lg hover:bg-primary/90 transition disabled:opacity-50 disabled:cursor-not-allowed"
          >
            Done
          </button>
        </div>
      </div>
    </div>
  );
}

/** Confirmation modal for disabling MFA; requires the current OTP first. */
function UnenrolModal({ factorId, onClose, onDone }: { factorId: string; onClose: () => void; onDone: () => void }) {
  const [code, setCode] = useState("");
  const [shakeKey, setShakeKey] = useState(0);
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState("");
  const [countdown, setCountdown] = useState(0);

  useEffect(() => {
    if (countdown <= 0) return;
    const timer = setTimeout(() => setCountdown((c) => c - 1), 1000);
    return () => clearTimeout(timer);
  }, [countdown]);

  const handleVerifyAndRemove = async (otp?: string) => {
    const value = otp ?? code;
    if (!value || confirming || countdown > 0) return;
    setConfirming(true);
    setError("");

    const verification = await verifyTotpFactor(factorId, value);
    if (verification.rateLimited) {
      setCountdown(30);
      setError("Too many attempts. Please wait 30 seconds and try again.");
      setConfirming(false);
      return;
    }
    if (!verification.ok) {
      setError("Incorrect code. Please try again.");
      setShakeKey((k) => k + 1);
      setConfirming(false);
      return;
    }

    const { error: unenrolError } = await supabase.auth.mfa.unenroll({ factorId });
    if (unenrolError) {
      setError(unenrolError.message || "Could not remove two-factor authentication.");
      setConfirming(false);
      return;
    }

    await clearRecoveryCodes();
    onDone();
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
      <div className="bg-white rounded-2xl shadow-2xl w-full max-w-sm overflow-hidden">
        <div className="bg-red-600 px-6 py-4 text-white text-center">
          <h3 className="text-lg font-bold flex items-center justify-center gap-2">
            <ShieldOff className="w-5 h-5" />
            Remove two-factor authentication
          </h3>
        </div>
        <div className="p-6">
          <p className="text-sm text-muted-foreground mb-4">
            This will remove two-factor authentication from your account. Are you sure?
            <br />
            <span className="text-foreground font-medium">
              Enter the current 6-digit code from your authenticator app to confirm:
            </span>
          </p>

          <OtpInput
            autoFocus
            disabled={confirming || countdown > 0}
            shakeKey={shakeKey}
            onChange={setCode}
            onComplete={(value) => handleVerifyAndRemove(value)}
          />

          {countdown > 0 ? (
            <p className="text-center text-sm text-amber-600 mt-3">
              Too many attempts. Please wait {countdown} seconds and try again.
            </p>
          ) : (
            error && (
              <p className="text-center text-sm text-red-500 mt-3 flex items-center justify-center gap-1.5">
                <AlertTriangle className="w-4 h-4" />
                {error}
              </p>
            )
          )}

          <div className="flex gap-2 mt-5">
            <button
              type="button"
              onClick={onClose}
              disabled={confirming}
              className="flex-1 px-4 py-2.5 border border-border rounded-lg text-sm font-medium text-foreground hover:bg-gray-50 transition disabled:opacity-50"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={() => handleVerifyAndRemove()}
              disabled={confirming || code.length !== 6 || countdown > 0}
              className="flex-1 flex items-center justify-center gap-2 px-4 py-2.5 bg-red-600 text-white text-sm font-semibold rounded-lg hover:bg-red-700 transition disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {confirming ? <Loader2 className="w-4 h-4 animate-spin" /> : null}
              {confirming ? "Removing..." : "Verify & Remove"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

export default function MfaSection() {
  const { user } = useAuth();
  const [factors, setFactors] = useState<VerifiedFactor[] | null>(null);
  const [enrolment, setEnrolment] = useState<Enrolment | null>(null);
  const [starting, setStarting] = useState(false);
  const [enrolError, setEnrolError] = useState("");
  const [activating, setActivating] = useState(false);
  const [otpCode, setOtpCode] = useState("");
  const [otpShakeKey, setOtpShakeKey] = useState(0);
  const [recoveryCodes, setRecoveryCodes] = useState<string[] | null>(null);
  const [showUnenrol, setShowUnenrol] = useState(false);
  const [countdown, setCountdown] = useState(0);

  useEffect(() => {
    if (countdown <= 0) return;
    const timer = setTimeout(() => setCountdown((c) => c - 1), 1000);
    return () => clearTimeout(timer);
  }, [countdown]);

  const refreshFactors = useCallback(async () => {
    const verified = await listVerifiedFactors();
    setFactors(verified);
  }, []);

  useEffect(() => {
    if (user) refreshFactors();
    else setFactors([]);
  }, [user, refreshFactors]);

  const enabled = (factors?.length ?? 0) > 0;
  const factorId = factors?.[0]?.id;
  const signedIn = Boolean(user);

  const startEnrol = async () => {
    setStarting(true);
    setEnrolError("");
    try {
      const { data, error } = await supabase.auth.mfa.enroll({ factorType: "totp" });
      if (error || !data) throw error ?? new Error("Could not start setup.");
      setEnrolment({
        factorId: data.id,
        // qr_code from Supabase needs the data-URI prefix to render in <img>
        qrCode: `data:image/svg+xml;utf-8,${data.totp.qr_code}`,
        secret: data.totp.secret,
      });
    } catch (err) {
      setEnrolError(err instanceof Error ? err.message : "Could not start setup. Please try again.");
    } finally {
      setStarting(false);
    }
  };

  const activate = async (value?: string) => {
    const code = value ?? otpCode;
    if (!enrolment || code.length !== 6 || activating || countdown > 0) return;
    setActivating(true);
    setEnrolError("");
    const result = await verifyTotpFactor(enrolment.factorId, code);
    if (result.rateLimited) {
      setCountdown(30);
      setEnrolError("Too many attempts. Please wait 30 seconds and try again.");
      setActivating(false);
      return;
    }
    if (!result.ok) {
      setEnrolError("Incorrect code. Please try again.");
      setOtpShakeKey((k) => k + 1);
      setActivating(false);
      return;
    }

    // Activation succeeded: mint the recovery codes, store only their SHA-256
    // hashes, then show the plain-text codes exactly once.
    const codes = generateRecoveryCodes();
    const stored = await storeRecoveryCodes(codes);
    if (stored) {
      setEnrolError(stored);
      setActivating(false);
      return;
    }
    setEnrolment(null);
    setRecoveryCodes(codes);
    setOtpCode("");
    await refreshFactors();
    setActivating(false);
  };

  const closeRecoveryCodes = async () => {
    setRecoveryCodes(null);
  };

  if (factors === null) {
    return (
      <div className="bg-white border border-border rounded-2xl shadow-sm p-5 sm:p-6 flex items-center justify-center gap-2 text-sm text-muted-foreground">
        <Loader2 className="w-4 h-4 animate-spin" />
        Checking security settings...
      </div>
    );
  }

  return (
    <div className="bg-white border border-border rounded-2xl shadow-sm p-5 sm:p-6">
      <div className="flex items-center gap-3 mb-4">
        <div className="w-9 h-9 bg-primary/10 rounded-xl flex items-center justify-center">
          <ShieldCheck className="w-5 h-5 text-primary" />
        </div>
        <div>
          <h2 className="text-xl font-bold text-foreground">Security</h2>
          <p className="text-xs text-muted-foreground">
            {enabled ? (
              <span className="inline-flex items-center gap-1 text-green-600 font-medium">
                <Check className="w-3.5 h-3.5" /> Two-factor authentication: Enabled
              </span>
            ) : (
              <span className="inline-flex items-center gap-1 font-medium">
                <ShieldOff className="w-3.5 h-3.5" /> Two-factor authentication: Not enabled
              </span>
            )}
          </p>
        </div>
      </div>

      {enrolment ? (
        <div className="space-y-4 mt-4">
          <p className="text-sm text-muted-foreground">
            Scan this QR code with <span className="font-medium text-foreground">Google Authenticator, Authy, or any authenticator app</span>.
            Then enter the 6-digit code below to confirm.
          </p>
          <div className="flex justify-center">
            <img src={enrolment.qrCode} alt="TOTP QR code" className="w-44 h-44 border border-border rounded-xl p-2 bg-white" />
          </div>
          <div className="flex items-center gap-2 justify-center">
            <span className="font-mono text-sm tracking-widest text-muted-foreground">{enrolment.secret}</span>
            <button
              type="button"
              onClick={() => void navigator.clipboard?.writeText(enrolment.secret)}
              className="p-1.5 rounded-lg hover:bg-gray-100 text-muted-foreground hover:text-foreground transition"
              aria-label="Copy secret key"
            >
              <Copy className="w-4 h-4" />
            </button>
          </div>

          <div>
            <OtpInput autoFocus disabled={activating || countdown > 0} shakeKey={otpShakeKey} onChange={setOtpCode} onComplete={(value) => void activate(value)} />
          </div>
          {countdown > 0 ? (
            <p className="text-center text-sm text-amber-600 font-medium">
              Too many attempts. Please wait {countdown} seconds and try again.
            </p>
          ) : enrolError ? (
            <p className="text-center text-sm text-red-500 flex items-center justify-center gap-1.5">
              <AlertTriangle className="w-4 h-4" /> {enrolError}
            </p>
          ) : null}

          <div className="flex gap-2">
            <button
              type="button"
              onClick={() => setEnrolment(null)}
              disabled={activating}
              className="flex-1 px-4 py-2.5 border border-border rounded-lg text-sm font-medium text-foreground hover:bg-gray-50 transition disabled:opacity-50"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={() => void activate()}
              disabled={activating || countdown > 0 || otpCode.length !== 6}
              className="flex-1 flex items-center justify-center gap-2 px-4 py-2.5 bg-primary text-primary-foreground font-semibold rounded-lg hover:bg-primary/90 transition disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {activating ? <Loader2 className="w-4 h-4 animate-spin" /> : <Smartphone className="w-4 h-4" />}
              {activating ? "Verifying..." : "Verify and activate"}
            </button>
          </div>
        </div>
      ) : enabled ? (
        <div className="flex flex-wrap items-center justify-between gap-3 mt-1">
          <p className="text-sm text-muted-foreground">
            You are protected with an authenticator app. Removing it will reduce your account security.
          </p>
          <button
            type="button"
            onClick={() => setShowUnenrol(true)}
            className="inline-flex items-center gap-2 px-4 py-2 text-sm text-red-600 border border-red-200 rounded-lg hover:bg-red-50 transition"
          >
            <ShieldOff className="w-4 h-4" />
            Remove authenticator
          </button>
        </div>
      ) : !signedIn ? (
        <p className="text-sm text-muted-foreground mt-1">
          Sign in with your account to manage two-factor authentication.
        </p>
      ) : (
        <div className="flex flex-wrap items-center justify-between gap-3 mt-1">
          <p className="text-sm text-muted-foreground">
            Add an extra layer of security to your account so only you can sign in.
          </p>
          <button
            type="button"
            onClick={() => void startEnrol()}
            disabled={starting}
            className="inline-flex items-center gap-2 px-4 py-2 bg-primary text-primary-foreground font-semibold rounded-lg hover:bg-primary/90 transition disabled:opacity-60"
          >
            {starting ? <Loader2 className="w-4 h-4 animate-spin" /> : <Smartphone className="w-4 h-4" />}
            Set up authenticator app
          </button>
        </div>
      )}

      {enrolError && !enrolment && (
        <p className="mt-3 text-sm text-red-500 flex items-center gap-1.5">
          <AlertTriangle className="w-4 h-4" /> {enrolError}
        </p>
      )}

      {recoveryCodes && <RecoveryCodesModal codes={recoveryCodes} onDone={() => void closeRecoveryCodes()} />}
      {showUnenrol && factorId && (
        <UnenrolModal factorId={factorId} onClose={() => setShowUnenrol(false)} onDone={() => void refreshFactors().then(() => setShowUnenrol(false))} />
      )}
    </div>
  );
}