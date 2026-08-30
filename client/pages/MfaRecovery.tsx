/**
 * /mfa-recovery — lets a user who cannot reach their authenticator app sign
 * in with one of the one-time backup codes issued during enrolment.
 *
 * Supabase has no native recovery-code support, so this page is the
 * client-side counterpart of the SHA-256-hashed codes stored in the user's
 * metadata (see client/lib/mfa.ts). A successful code is burned immediately
 * (its hash is removed) and the session is only elevated in memory.
 */

import { useState } from "react";
import { useLocation, useNavigate, Navigate, Link } from "react-router-dom";
import { useAuth } from "@/context/AuthContext";
import { consumeRecoveryCode } from "@/lib/mfa";
import { Loader2, KeyRound, LogOut } from "lucide-react";
import { supabase } from "@/lib/supabaseClient";

export default function MfaRecovery() {
  const navigate = useNavigate();
  const location = useLocation();
  const { user, loading } = useAuth();
  const [code, setCode] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  const from = (location.state as { from?: string } | null)?.from || "/";

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!code.trim() || submitting) return;
    setSubmitting(true);
    setError("");

    const result = await consumeRecoveryCode(code);
    setSubmitting(false);

    if (result.valid) {
      navigate(from, { replace: true });
      return;
    }
    setError("Invalid or already used recovery code.");
  };

  const handleSignOut = async () => {
    await supabase.auth.signOut();
    navigate("/login", { replace: true });
  };

  if (loading) {
    return (
      <div className="min-h-[60vh] flex items-center justify-center">
        <Loader2 className="w-6 h-6 animate-spin text-primary" />
      </div>
    );
  }

  if (!user) {
    return <Navigate to="/login" replace />;
  }

  return (
    <div className="min-h-screen flex items-center justify-center px-4 py-12 bg-gradient-to-br from-primary/5 via-accent/5 to-secondary/5">
      <div className="w-full max-w-md">
        <div className="bg-white rounded-2xl shadow-xl border border-border overflow-hidden">
          <div className="bg-gradient-to-r from-primary to-accent px-8 py-6 text-white text-center">
            <div className="flex justify-center mb-3">
              <div className="bg-white/20 rounded-full p-3">
                <KeyRound className="w-7 h-7 text-white" />
              </div>
            </div>
            <h1 className="text-2xl font-bold">Recovery Code</h1>
            <p className="text-white/80 text-sm mt-1">Sign in with one of your one-time backup codes</p>
          </div>

          <div className="px-8 py-8 space-y-5">
            <form onSubmit={handleSubmit} className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-foreground mb-1.5" htmlFor="recovery-code">
                  Recovery code
                </label>
                <input
                  id="recovery-code"
                  type="text"
                  autoFocus
                  autoComplete="off"
                  autoCapitalize="characters"
                  spellCheck={false}
                  placeholder="XXXX-0000"
                  value={code}
                  onChange={(e) => {
                    setCode(e.target.value.toUpperCase());
                    setError("");
                  }}
                  className="w-full px-4 py-3 border border-border rounded-lg text-base tracking-widest font-mono text-center focus:outline-none focus:ring-2 focus:ring-primary/40 focus:border-primary transition"
                />
              </div>

              {error && <p className="text-red-500 text-sm">{error}</p>}

              <button
                type="submit"
                disabled={submitting || !code.trim()}
                className="w-full py-2.5 bg-primary text-primary-foreground font-semibold rounded-lg hover:bg-primary/90 transition disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-2"
              >
                {submitting ? <Loader2 className="w-4 h-4 animate-spin" /> : null}
                {submitting ? "Checking..." : "Continue"}
              </button>
            </form>

            <Link to="/mfa-challenge" state={{ from }} className="block text-center text-sm text-primary font-semibold hover:underline">
              Back to authenticator code
            </Link>

            <button
              type="button"
              onClick={() => void handleSignOut()}
              className="w-full flex items-center justify-center gap-2 px-4 py-2.5 border border-border rounded-lg text-sm text-muted-foreground hover:bg-gray-50 transition"
            >
              <LogOut className="w-4 h-4" />
              Sign out
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}