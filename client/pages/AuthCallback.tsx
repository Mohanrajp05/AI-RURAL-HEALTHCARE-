import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { supabase } from "@/lib/supabaseClient";
import { mfaChallengeRequired } from "@/lib/mfa";
import { Loader2 } from "lucide-react";

export default function AuthCallback() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;

    const handleCallback = async () => {
      const code = searchParams.get("code");
      if (code) {
        try {
          const { error: exchangeError } = await supabase.auth.exchangeCodeForSession(code);
          if (exchangeError) throw exchangeError;
        } catch (err) {
          if (!cancelled) {
            setError(err instanceof Error ? err.message : "Authentication failed.");
          }
          return;
        }
      }

      const { data } = await supabase.auth.getSession();
      if (cancelled) return;
      if (data.session) {
        // OAuth sign-in lands here; if the account has MFA enrolled, step up
        // through the 2FA challenge before going anywhere else.
        const needsMfa = await mfaChallengeRequired();
        navigate(needsMfa ? "/mfa-challenge" : "/", { replace: true });
      } else {
        navigate("/login", { replace: true });
      }
    };

    handleCallback();
    return () => {
      cancelled = true;
    };
  }, [navigate, searchParams]);

  return (
    <div className="min-h-[60vh] flex flex-col items-center justify-center gap-4 px-4">
      {error ? (
        <p className="text-red-600 text-sm text-center max-w-md">{error}</p>
      ) : (
        <p className="text-sm text-muted-foreground flex items-center gap-2">
          <Loader2 className="w-4 h-4 animate-spin" />
          Completing sign in...
        </p>
      )}
    </div>
  );
}