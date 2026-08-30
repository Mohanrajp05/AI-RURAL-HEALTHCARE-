/**
 * /mfa-challenge — the two-factor step shown right after a successful
 * email/password or OAuth sign-in whenever the user has a verified TOTP
 * factor but their session is only at aal1.
 *
 * Mirrors the Google/GitHub 2FA page: six boxes that auto-advance and
 * auto-submit, inline errors with a shake, a recovery-code escape hatch, and
 * a rate-limit countdown when Supabase starts throttling verify attempts.
 */

import { useEffect, useState } from "react";
import { useLocation, useNavigate, Link } from "react-router-dom";
import { useAuth } from "@/context/AuthContext";
import { supabase } from "@/lib/supabaseClient";
import {
  listVerifiedFactors,
  verifyTotpFactor,
  isRecoveryActive,
} from "@/lib/mfa";
import { Loader2, ShieldCheck, LogOut, AlertTriangle } from "lucide-react";
import OtpInput from "@/components/mfa/OtpInput";

export default function MfaChallenge() {
  const navigate = useNavigate();
  const location = useLocation();
  const { user, loading } = useAuth();
  const [factorId, setFactorId] = useState<string | null>(null);
  const [checking, setChecking] = useState(true);
  const [noFactor, setNoFactor] = useState(false);
  const [verifying, setVerifying] = useState(false);
  const [error, setError] = useState("");
  const [shakeKey, setShakeKey] = useState(0);
  const [countdown, setCountdown] = useState(0);
  const [otpCode, setOtpCode] = useState("");

  const from = (location.state as { from?: string } | null)?.from || "/";

  useEffect(() => {
    if (countdown <= 0) return;
    const timer = setTimeout(() => setCountdown((c) => c - 1), 1000);
    return () => clearTimeout(timer);
  }, [countdown]);

  useEffect(() => {
    if (loading) return;
    if (!user) {
      navigate("/login", { replace: true });
      return;
    }
    if (isRecoveryActive()) {
      navigate(from, { replace: true });
      return;
    }
    void (async () => {
      const factors = await listVerifiedFactors();
      if (factors.length === 0) {
        setNoFactor(true);
        setChecking(false);
        return;
      }
      setFactorId(factors[0].id);
      setChecking(false);
    })();
  }, [user, loading, navigate, from]);

  const handleVerify = async (code: string) => {
    if (!factorId || verifying || countdown > 0) return;
    setVerifying(true);
    setError("");

    const result = await verifyTotpFactor(factorId, code);
    setVerifying(false);

    if (result.ok) {
      navigate(from, { replace: true });
      return;
    }
    if (result.rateLimited) {
      setCountdown(30);
      setError("Too many attempts. Please wait 30 seconds and try again.");
      return;
    }
    setError("Incorrect code. Please try again.");
    setShakeKey((k) => k + 1);
  };

  const handleSignOut = async () => {
    await supabase.auth.signOut();
    navigate("/login", { replace: true });
  };

  return (
    <div className="min-h-screen flex items-center justify-center px-4 py-12 bg-gradient-to-br from-primary/5 via-accent/5 to-secondary/5">
      <div className="w-full max-w-md">
        <div className="bg-white rounded-2xl shadow-xl border border-border overflow-hidden">
          <div className="bg-gradient-to-r from-primary to-accent px-8 py-6 text-white text-center">
            <div className="flex justify-center mb-3">
              <div className="bg-white/20 rounded-full p-3">
                <ShieldCheck className="w-7 h-7 text-white" />
              </div>
            </div>
            <h1 className="text-2xl font-bold">Two-Factor Authentication</h1>
            <p className="text-white/80 text-sm mt-1">Enter the 6-digit code from your authenticator app</p>
          </div>

          <div className="px-8 py-8 space-y-5">
            {checking ? (
              <div className="flex justify-center py-8">
                <Loader2 className="w-6 h-6 animate-spin text-primary" />
              </div>
            ) : noFactor ? (
              <div className="text-center space-y-4">
                <p className="text-sm text-muted-foreground">
                  No two-factor authentication is set up on this account, so no code is required.
                </p>
                <button
                  type="button"
                  onClick={() => navigate(from, { replace: true })}
                  className="w-full py-2.5 bg-primary text-primary-foreground font-semibold rounded-lg hover:bg-primary/90 transition"
                >
                  Continue
                </button>
              </div>
            ) : (
              <>
                <div>
                  <OtpInput
                    autoFocus
                    disabled={verifying || countdown > 0}
                    shakeKey={shakeKey}
                    onChange={setOtpCode}
                    onComplete={(code) => void handleVerify(code)}
                  />
                </div>

                {countdown > 0 ? (
                  <p className="text-center text-sm text-amber-600 font-medium">
                    Too many attempts. Please wait {countdown} seconds and try again.
                  </p>
                ) : error ? (
                  <p className="text-center text-sm text-red-500 flex items-center justify-center gap-1.5">
                    <AlertTriangle className="w-4 h-4" /> {error}
                  </p>
                ) : null}

                <button
                  type="button"
                  onClick={() => void handleVerify(otpCode)}
                  disabled={verifying || countdown > 0 || otpCode.length !== 6}
                  className="w-full py-2.5 bg-primary text-primary-foreground font-semibold rounded-lg hover:bg-primary/90 transition disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  Verify
                </button>

                <Link
                  to="/mfa-recovery"
                  state={{ from }}
                  className="block text-center text-sm text-primary font-semibold hover:underline"
                >
                  Use a recovery code instead
                </Link>

                <button
                  type="button"
                  onClick={() => void handleSignOut()}
                  className="w-full flex items-center justify-center gap-2 px-4 py-2.5 border border-border rounded-lg text-sm text-muted-foreground hover:bg-gray-50 transition"
                >
                  <LogOut className="w-4 h-4" />
                  Sign out
                </button>
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}