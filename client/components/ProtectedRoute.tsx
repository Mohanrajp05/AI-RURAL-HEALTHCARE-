/**
 * Route guard for pages that require a signed-in user. On top of the plain
 * session check it also enforces the MFA (AAL2) requirement:
 *
 *   - signed in at aal2, or with no factor enrolled  -> render the page
 *   - signed in at aal1 with a verified TOTP factor  -> redirect to
 *     /mfa-challenge so the user proves possession of the factor first
 *
 * This gives the app per-route AAL2 without touching Supabase's
 * project-wide AAL policy, which would lock EVERYONE out of all
 * authenticated requests until they enrol.
 */

import { useEffect, useState } from "react";
import { Navigate, useLocation } from "react-router-dom";
import type { ReactNode } from "react";
import { useAuth } from "@/context/AuthContext";
import { mfaChallengeRequired } from "@/lib/mfa";
import { Loader2 } from "lucide-react";

export function ProtectedRoute({ children }: { children: ReactNode }) {
  const { user, loading } = useAuth();
  const location = useLocation();
  const [mfaChecking, setMfaChecking] = useState(true);
  const [needsMfa, setNeedsMfa] = useState(false);

  useEffect(() => {
    let mounted = true;
    if (!user) {
      setMfaChecking(false);
      return;
    }
    mfaChallengeRequired()
      .then((required) => {
        if (!mounted) return;
        setNeedsMfa(required);
        setMfaChecking(false);
      })
      .catch(() => {
        if (mounted) setMfaChecking(false);
      });
    return () => {
      mounted = false;
    };
  }, [user]);

  if (loading || (user && mfaChecking)) {
    return (
      <div className="min-h-[60vh] flex items-center justify-center">
        <Loader2 className="w-6 h-6 animate-spin text-primary" />
      </div>
    );
  }

  if (!user) {
    return (
      <Navigate
        to="/login"
        replace
        state={{ from: location.pathname, message: "Please sign in to use this feature" }}
      />
    );
  }

  if (needsMfa) {
    return <Navigate to="/mfa-challenge" replace state={{ from: location.pathname }} />;
  }

  return <>{children}</>;
}