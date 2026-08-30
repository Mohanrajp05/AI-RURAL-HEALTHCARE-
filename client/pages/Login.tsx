import { Layout } from "@/components/Layout";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { useState } from "react";
import { AlertCircle, CheckCircle2, Loader2, Eye, EyeOff, Heart, Lock, Mail, X } from "lucide-react";
import { supabase } from "@/lib/supabaseClient";
import { mfaChallengeRequired, markRecoveryActive } from "@/lib/mfa";

const BACKEND = import.meta.env.VITE_BACKEND_URL || "http://127.0.0.1:5001";

const GMAIL_REGEX = /^[A-Za-z0-9._%+-]+@gmail\.com$/i;

function GoogleIcon() {
  return (
    <svg viewBox="0 0 24 24" className="w-5 h-5" xmlns="http://www.w3.org/2000/svg">
      <path d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z" fill="#4285F4"/>
      <path d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" fill="#34A853"/>
      <path d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l3.66-2.84z" fill="#FBBC05"/>
      <path d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" fill="#EA4335"/>
    </svg>
  );
}

function GitHubIcon() {
  return (
    <svg viewBox="0 0 24 24" className="w-5 h-5" fill="currentColor" xmlns="http://www.w3.org/2000/svg">
      <path d="M12 .5C5.73.5.5 5.73.5 12c0 5.08 3.29 9.39 7.86 10.91.58.11.79-.25.79-.56 0-.27-.01-1.17-.02-2.12-3.2.7-3.88-1.36-3.88-1.36-.52-1.33-1.28-1.68-1.28-1.68-1.04-.71.08-.7.08-.7 1.15.08 1.76 1.18 1.76 1.18 1.03 1.76 2.69 1.25 3.35.96.1-.75.4-1.25.72-1.54-2.55-.29-5.24-1.28-5.24-5.68 0-1.26.45-2.28 1.18-3.09-.12-.29-.51-1.46.11-3.05 0 0 .97-.31 3.17 1.18a11.1 11.1 0 0 1 5.78 0c2.2-1.49 3.17-1.18 3.17-1.18.62 1.59.23 2.76.11 3.05.73.81 1.18 1.83 1.18 3.09 0 4.41-2.69 5.38-5.26 5.66.41.36.78 1.06.78 2.14 0 1.54-.01 2.79-.01 3.17 0 .31.21.67.8.56A10.52 10.52 0 0 0 23.5 12C23.5 5.73 18.27.5 12 .5z"/>
    </svg>
  );
}

interface GoogleModalProps {
  onClose: () => void;
  onSuccess: (user: { fullName: string; email: string; googleAuth: boolean }) => void;
}

function GoogleSignInModal({ onClose, onSuccess }: GoogleModalProps) {
  const [step, setStep] = useState<"email" | "password">("email");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [loading, setLoading] = useState(false);
  const [emailError, setEmailError] = useState("");
  const [passwordError, setPasswordError] = useState("");

  const guessedName = email
    .split("@")[0]
    .replace(/[._]/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());

  const handleEmailNext = (e: React.FormEvent) => {
    e.preventDefault();
    if (!GMAIL_REGEX.test(email.trim())) {
      setEmailError("Enter a valid Gmail address ending with @gmail.com.");
      return;
    }
    setStep("password");
  };

  const handleConfirm = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!password) {
      setPasswordError("Enter your password.");
      return;
    }

    setLoading(true);
    setPasswordError("");
    try {
      const response = await fetch(`${BACKEND}/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: email.trim(), password }),
      });
      const data = await response.json();
      if (!response.ok || !data.success) {
        setPasswordError(data.error || "Invalid email or password.");
        return;
      }
      onSuccess({
        fullName: data?.user?.fullName || guessedName || email.split("@")[0],
        email: data?.user?.email || email.trim(),
        googleAuth: true,
      });
    } catch {
      setPasswordError("Cannot connect to server. Please make sure the backend is running.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
      <div className="bg-white rounded-2xl shadow-2xl w-full max-w-sm relative overflow-hidden">
        <button
          onClick={onClose}
          className="absolute top-4 right-4 text-gray-400 hover:text-gray-600 transition"
          aria-label="Close"
        >
          <X className="w-5 h-5" />
        </button>

        <div className="px-8 pt-8 pb-6">
          {/* Google branding */}
          <div className="flex justify-center mb-4">
            <GoogleIcon />
          </div>
          <h2 className="text-xl font-semibold text-gray-800 text-center mb-1">
            {step === "email" ? "Sign in with Google" : "Confirm your account"}
          </h2>
          <p className="text-sm text-gray-500 text-center mb-6">
            {step === "email" ? "Use your Google Account" : "Enter your password"}
          </p>

          {step === "email" ? (
            <form onSubmit={handleEmailNext} className="space-y-4">
              <div>
                <input
                  type="email"
                  autoFocus
                  placeholder="Email or phone"
                  value={email}
                  onChange={(e) => { setEmail(e.target.value); setEmailError(""); }}
                  className="w-full px-4 py-3 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500/40 focus:border-blue-500 transition"
                  required
                />
                {emailError && <p className="text-red-500 text-xs mt-1">{emailError}</p>}
              </div>
              <p className="text-xs text-gray-500">
                Not your computer? Use guest mode to sign in privately.
              </p>
              <div className="flex justify-between items-center pt-2">
                <a href="https://accounts.google.com" target="_blank" rel="noopener noreferrer" className="text-blue-600 text-sm font-medium hover:underline">
                  Create account
                </a>
                <button
                  type="submit"
                  className="px-6 py-2 bg-blue-600 text-white text-sm font-semibold rounded-lg hover:bg-blue-700 transition"
                >
                  Next
                </button>
              </div>
            </form>
          ) : (
            <form onSubmit={handleConfirm} className="space-y-4">
              <div className="flex items-center gap-3 p-3 border border-gray-200 rounded-full">
                <div className="w-9 h-9 rounded-full bg-blue-100 flex items-center justify-center text-blue-700 font-bold text-sm flex-shrink-0">
                  {(guessedName || email).charAt(0).toUpperCase()}
                </div>
                <div className="min-w-0">
                  <p className="text-sm font-medium text-gray-800 truncate">{guessedName || email.split("@")[0]}</p>
                  <p className="text-xs text-gray-500 truncate">{email}</p>
                </div>
              </div>

              <div className="relative">
                <Lock className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
                <input
                  type={showPassword ? "text" : "password"}
                  value={password}
                  onChange={(e) => {
                    setPassword(e.target.value);
                    setPasswordError("");
                  }}
                  placeholder="Enter your password"
                  className="w-full pl-10 pr-10 py-3 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500/40 focus:border-blue-500 transition"
                  required
                />
                <button
                  type="button"
                  onClick={() => setShowPassword((v) => !v)}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-500 hover:text-gray-700"
                >
                  {showPassword ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                </button>
              </div>
              {passwordError && <p className="text-red-500 text-xs">{passwordError}</p>}

              <div className="flex justify-between items-center pt-2">
                <button
                  type="button"
                  onClick={() => {
                    setStep("email");
                    setPassword("");
                    setPasswordError("");
                  }}
                  className="text-blue-600 text-sm font-medium hover:underline"
                >
                  Back
                </button>
                <button
                  type="submit"
                  disabled={loading}
                  className="px-6 py-2 bg-blue-600 text-white text-sm font-semibold rounded-lg hover:bg-blue-700 transition"
                >
                  {loading ? "Checking..." : "Continue"}
                </button>
              </div>
            </form>
          )}
        </div>
      </div>
    </div>
  );
}

export default function Login() {
  const navigate = useNavigate();
  const location = useLocation();
  const [formData, setFormData] = useState({ email: "", password: "" });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [showGoogleModal, setShowGoogleModal] = useState(false);

  const redirectMessage = (location.state as { message?: string } | null)?.message;
  // Set by Register.tsx's signUp() emailRedirectTo -- lands here (instead
  // of Home, the previous default) after the user clicks the confirmation
  // link in their email.
  const emailConfirmed = new URLSearchParams(location.search).get("confirmed") === "true";

  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const { name, value } = e.target;
    setFormData((prev) => ({ ...prev, [name]: value }));
    setError("");
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const from = (location.state as { from?: string } | null)?.from;
    if (!GMAIL_REGEX.test(formData.email.trim())) {
      setError("Please enter a valid Gmail address ending with @gmail.com.");
      return;
    }
    setLoading(true);
    setError("");
    try {
      markRecoveryActive(false);
      const { data, error: signInError } = await supabase.auth.signInWithPassword({
        email: formData.email.trim(),
        password: formData.password,
      });
      if (signInError) {
        setError(signInError.message || "Invalid email or password.");
        return;
      }
      if (data.session) {
        // MFA step: if this account has a verified TOTP factor but the new
        // session is only aal1, send the user through the 2FA page first.
        const needsMfa = await mfaChallengeRequired();
        if (needsMfa) {
          navigate("/mfa-challenge", { replace: true, state: { from } });
          return;
        }
        const user = data.session.user;
        const fullName = user.user_metadata?.full_name || formData.email.split("@")[0];
        localStorage.setItem("user", JSON.stringify({ fullName, email: user.email || formData.email.trim(), googleAuth: false }));
        // No backend sync needed -- Supabase Auth is the only place user
        // profiles live now (see backend/supabase_admin.py). The old
        // MySQL `users` mirror was removed because it only ever synced
        // from this email/password flow, never from OAuth, so it was
        // permanently missing most real users.
        navigate(from || "/", { replace: true });
      } else {
        setError("Please check your email to confirm your account before signing in.");
      }
    } catch {
      setError("Something went wrong. Please try again.");
    } finally {
      setLoading(false);
    }
  };

  const handleOAuth = async (provider: "google" | "github") => {
    setError("");
    setLoading(true);
    try {
      const { error } = await supabase.auth.signInWithOAuth({
        provider,
        options: { redirectTo: window.location.origin },
      });
      if (error) setError(error.message);
    } catch {
      setError("Could not start sign in with this provider. Please try again.");
    } finally {
      setLoading(false);
    }
  };

  const handleGoogleSuccess = (user: { fullName: string; email: string; googleAuth: boolean }) => {
    localStorage.setItem("user", JSON.stringify(user));
    setShowGoogleModal(false);
    navigate("/");
  };

  return (
    <Layout>
      {showGoogleModal && (
        <GoogleSignInModal
          onClose={() => setShowGoogleModal(false)}
          onSuccess={handleGoogleSuccess}
        />
      )}

      <div className="min-h-[80vh] flex items-center justify-center px-4 py-12">
        <div className="w-full max-w-md">
          <div className="bg-white rounded-2xl shadow-xl border border-border overflow-hidden">
            {/* Header */}
            <div className="bg-gradient-to-r from-primary to-accent px-8 py-6 text-white text-center">
              <div className="flex justify-center mb-3">
                <div className="bg-white/20 rounded-full p-3">
                  <Heart className="w-7 h-7 text-white" />
                </div>
              </div>
              <h1 className="text-2xl font-bold">Welcome Back</h1>
              <p className="text-white/80 text-sm mt-1">Sign in to Rural Healthcare System</p>
            </div>

            <div className="px-8 py-8 space-y-5">
              {emailConfirmed && (
                <div className="flex items-start gap-3 p-3 bg-emerald-50 border border-emerald-200 rounded-lg">
                  <CheckCircle2 className="w-5 h-5 text-emerald-500 flex-shrink-0 mt-0.5" />
                  <p className="text-emerald-700 text-sm">
                    Your email has been confirmed! Please sign in to continue.
                  </p>
                </div>
              )}
              {redirectMessage && (
                <div className="flex items-start gap-3 p-3 bg-amber-50 border border-amber-200 rounded-lg">
                  <AlertCircle className="w-5 h-5 text-amber-500 flex-shrink-0 mt-0.5" />
                  <p className="text-amber-700 text-sm">{redirectMessage}</p>
                </div>
              )}
              {error && (
                <div className="flex items-start gap-3 p-3 bg-red-50 border border-red-200 rounded-lg">
                  <AlertCircle className="w-5 h-5 text-red-500 flex-shrink-0 mt-0.5" />
                  <p className="text-red-700 text-sm">{error}</p>
                </div>
              )}

              <form onSubmit={handleSubmit} className="space-y-4">
                <div>
                  <label className="block text-sm font-medium text-foreground mb-1.5" htmlFor="email">
                    Email Address
                  </label>
                  <div className="relative">
                    <Mail className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
                    <input
                      id="email"
                      type="email"
                      name="email"
                      placeholder="you@example.com"
                      value={formData.email}
                      onChange={handleChange}
                      pattern="[A-Za-z0-9._%+-]+@gmail\.com"
                      title="Use a valid Gmail address ending with @gmail.com"
                      className="w-full pl-10 pr-4 py-2.5 border border-border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-primary/40 focus:border-primary transition"
                      required
                    />
                  </div>
                </div>

                <div>
                  <label className="block text-sm font-medium text-foreground mb-1.5" htmlFor="password">
                    Password
                  </label>
                  <div className="relative">
                    <Lock className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
                    <input
                      id="password"
                      type={showPassword ? "text" : "password"}
                      name="password"
                      placeholder="Enter your password"
                      value={formData.password}
                      onChange={handleChange}
                      className="w-full pl-10 pr-10 py-2.5 border border-border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-primary/40 focus:border-primary transition"
                      required
                    />
                    <button
                      type="button"
                      onClick={() => setShowPassword((v) => !v)}
                      className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground transition"
                      tabIndex={-1}
                    >
                      {showPassword ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                    </button>
                  </div>
                </div>

                <button
                  type="submit"
                  disabled={loading || !formData.email || !formData.password}
                  className="w-full py-2.5 bg-primary text-primary-foreground font-semibold rounded-lg hover:bg-primary/90 transition disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-2"
                >
                  {loading ? (
                    <>
                      <Loader2 className="w-4 h-4 animate-spin" />
                      Signing inâ€¦
                    </>
                  ) : (
                    "Sign In"
                  )}
                </button>
              </form>

              <div className="flex items-center gap-3">
                <div className="flex-1 h-px bg-border" />
                <span className="text-xs text-muted-foreground">OR</span>
                <div className="flex-1 h-px bg-border" />
              </div>

              {/* Google Sign-In Button */}
              <button
                type="button"
                onClick={() => handleOAuth("google")}
                className="w-full flex items-center justify-center gap-3 px-4 py-2.5 border border-gray-300 rounded-lg bg-white hover:bg-gray-50 shadow-sm transition text-gray-700 font-medium text-sm"
              >
                <GoogleIcon />
                Sign in with Google
              </button>

              {/* GitHub Sign-In Button */}
              <button
                type="button"
                onClick={() => handleOAuth("github")}
                className="w-full flex items-center justify-center gap-3 px-4 py-2.5 border border-transparent rounded-lg bg-[#24292e] hover:bg-[#2f363d] shadow-sm transition text-white font-medium text-sm"
              >
                <GitHubIcon />
                Sign in with GitHub
              </button>

              <p className="text-center text-sm text-muted-foreground">
                Don't have an account?{" "}
                <Link to="/register" className="text-primary font-semibold hover:underline">
                  Create one
                </Link>
              </p>
            </div>
          </div>
        </div>
      </div>
    </Layout>
  );
}
