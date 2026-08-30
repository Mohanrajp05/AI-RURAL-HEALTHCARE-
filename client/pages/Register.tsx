import { Layout } from "@/components/Layout";
import { Link, useNavigate } from "react-router-dom";
import { useRef, useState } from "react";
import { AlertCircle, Check, CheckCircle2, Circle, Loader2, Eye, EyeOff, Heart, Lock, Mail, User, X } from "lucide-react";
import { supabase } from "@/lib/supabaseClient";
import { checkPasswordList, passwordStrength, validateConfirmPassword, validateEmail, validateName, validatePassword } from "@/utils/validation";

const BACKEND = import.meta.env.VITE_BACKEND_URL || "http://localhost:5001";

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

const STRENGTH_BAR = ["bg-red-500", "bg-red-500", "bg-amber-500", "bg-lime-500", "bg-emerald-500"];
const STRENGTH_TEXT = ["text-red-500", "text-red-500", "text-amber-500", "text-lime-600", "text-emerald-600"];

interface GoogleModalProps {
  onClose: () => void;
  onSuccess: (user: { fullName: string; email: string; googleAuth: boolean }) => void;
}

function GoogleSignInModal({ onClose, onSuccess }: GoogleModalProps) {
  const [step, setStep] = useState<"email" | "password">("email");
  const [email, setEmail] = useState("");
  const [fullName, setFullName] = useState("");
  const [password, setPassword] = useState("");
  const [emailError, setEmailError] = useState("");
  const [passwordError, setPasswordError] = useState("");
  const [loading, setLoading] = useState(false);
  const [showPassword, setShowPassword] = useState(false);

  const handleEmailNext = (e: React.FormEvent) => {
    e.preventDefault();
    if (!email.includes("@")) {
      setEmailError("Enter a valid email address.");
      return;
    }
    const guessedName = email.split("@")[0].replace(/[._]/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
    setFullName(guessedName);
    setStep("password");
  };

  const handleConfirm = (e: React.FormEvent) => {
    e.preventDefault();
    if (password.length < 6) {
      setPasswordError("Password must be at least 6 characters.");
      return;
    }

    setLoading(true);
    setPasswordError("");
    fetch(`${BACKEND}/register`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        fullName: fullName || email.split("@")[0],
        email,
        password,
      }),
    })
      .then(async (response) => {
        const data = await response.json();
        if (!response.ok || !data.success) {
          throw new Error(data.error || "Registration failed.");
        }
        onSuccess({ fullName: data?.user?.fullName || fullName || email.split("@")[0], email, googleAuth: true });
      })
      .catch((err) => {
        setPasswordError(err instanceof Error ? err.message : "Registration failed.");
      })
      .finally(() => setLoading(false));
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
      <div className="bg-white rounded-2xl shadow-2xl w-full max-w-sm relative overflow-hidden">
        <button onClick={onClose} className="absolute top-4 right-4 text-gray-400 hover:text-gray-600 transition" aria-label="Close">
          <X className="w-5 h-5" />
        </button>
        <div className="px-8 pt-8 pb-6">
          <div className="flex justify-center mb-4"><GoogleIcon /></div>
          <h2 className="text-xl font-semibold text-gray-800 text-center mb-1">
            {step === "email" ? "Sign up with Google" : "Confirm your account"}
          </h2>
          <p className="text-sm text-gray-500 text-center mb-6">
            {step === "email" ? "Use your Google Account" : "Continue as this user"}
          </p>
          {step === "email" ? (
            <form onSubmit={handleEmailNext} className="space-y-4">
              <div>
                <input
                  type="email"
                  autoFocus
                  placeholder="Email or phone"
                  value={email}
                  onChange={(e) => {
                    setEmail(e.target.value);
                    setEmailError("");
                  }}
                  className="w-full px-4 py-3 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500/40 focus:border-blue-500 transition"
                  required
                />
                {emailError && <p className="text-red-500 text-xs mt-1">{emailError}</p>}
              </div>
              <p className="text-xs text-gray-500">Not your computer? Use guest mode to sign in privately.</p>
              <div className="flex justify-between items-center pt-2">
                <a href="https://accounts.google.com" target="_blank" rel="noopener noreferrer" className="text-blue-600 text-sm font-medium hover:underline">
                  Create account
                </a>
                <button type="submit" className="px-6 py-2 bg-blue-600 text-white text-sm font-semibold rounded-lg hover:bg-blue-700 transition">
                  Next
                </button>
              </div>
            </form>
          ) : (
            <form onSubmit={handleConfirm} className="space-y-4">
              <div className="flex items-center gap-3 p-3 border border-gray-200 rounded-full">
                <div className="w-9 h-9 rounded-full bg-blue-100 flex items-center justify-center text-blue-700 font-bold text-sm flex-shrink-0">
                  {fullName.charAt(0).toUpperCase()}
                </div>
                <div className="min-w-0">
                  <p className="text-sm font-medium text-gray-800 truncate">{fullName}</p>
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
                  placeholder="Create a password"
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

              <p className="text-xs text-gray-500">Create a password before continuing.</p>
              <div className="flex justify-between items-center pt-2">
                <button type="button" onClick={() => setStep("email")} className="text-blue-600 text-sm font-medium hover:underline">
                  Back
                </button>
                <button type="submit" disabled={loading} className="px-6 py-2 bg-blue-600 text-white text-sm font-semibold rounded-lg hover:bg-blue-700 transition disabled:opacity-60">
                  {loading ? "Creating..." : "Continue"}
                </button>
              </div>
            </form>
          )}
        </div>
      </div>
    </div>
  );
}

export default function Register() {
  const navigate = useNavigate();
  const [formData, setFormData] = useState({ fullName: "", email: "", password: "", confirmPassword: "" });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [showConfirm, setShowConfirm] = useState(false);
  const [showGoogleModal, setShowGoogleModal] = useState(false);
  const [touched, setTouched] = useState<Record<string, boolean>>({});
  const [submitAttempted, setSubmitAttempted] = useState(false);

  const nameRef = useRef<HTMLInputElement>(null);
  const emailRef = useRef<HTMLInputElement>(null);
  const passwordRef = useRef<HTMLInputElement>(null);
  const confirmRef = useRef<HTMLInputElement>(null);

  const nameError = validateName(formData.fullName);
  const emailError = validateEmail(formData.email);
  const passwordError = validatePassword(formData.password);
  const confirmError = validateConfirmPassword(formData.confirmPassword, formData.password);
  const hasErrors = Boolean(nameError || emailError || passwordError || confirmError);
  const strength = formData.password ? passwordStrength(formData.password) : null;
  const checklist = formData.password ? checkPasswordList(formData.password) : [];

  const showFieldError = (field: string) => Boolean((touched[field] || submitAttempted) && field === "fullName" ? nameError : field === "email" ? emailError : field === "password" ? passwordError : confirmError);

  const fieldClass = (field: string) =>
    showFieldError(field)
      ? "border-red-500 focus:ring-red-500/30 focus:border-red-500"
      : "border-border focus:ring-primary/40 focus:border-primary";

  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const { name, value } = e.target;
    setFormData((prev) => ({ ...prev, [name]: value }));
    setError("");
  };

  const handleFieldBlur = (e: React.FocusEvent<HTMLInputElement>) => {
    const { name } = e.target;
    setTouched((prev) => ({ ...prev, [name]: true }));
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setSuccess("");
    setSubmitAttempted(true);
    setTouched({ fullName: true, email: true, password: true, confirmPassword: true });

    if (nameError) return nameRef.current?.focus();
    if (emailError) return emailRef.current?.focus();
    if (passwordError) return passwordRef.current?.focus();
    if (confirmError) return confirmRef.current?.focus();

    setLoading(true);
    try {
      const { data, error } = await supabase.auth.signUp({
        email: formData.email.trim(),
        password: formData.password,
        options: {
          data: { full_name: formData.fullName.trim() },
          // Without this, Supabase's confirmation-email link falls back to
          // the Site URL (Home) with no acknowledgement of what just
          // happened -- send confirmed users to Login instead, flagged so
          // it can show a "your email is confirmed" message there.
          emailRedirectTo: `${window.location.origin}/login?confirmed=true`,
        },
      });
      if (error) {
        setError(error.message || "Registration failed. Please try again.");
        return;
      }
      // No backend sync needed -- Supabase Auth is the only place user
      // profiles live now (see backend/supabase_admin.py). The old
      // MySQL `users` mirror was removed: it only ever synced from
      // email/password flows, never from OAuth, so it was permanently
      // missing most real users.
      setSuccess("Check your email to confirm your account.");
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
    setShowGoogleModal(false);
    setSuccess("Account created! Redirecting to login...");
    setTimeout(() => navigate("/login"), 1800);
  };

  return (
    <Layout>
      {showGoogleModal && (
        <GoogleSignInModal onClose={() => setShowGoogleModal(false)} onSuccess={handleGoogleSuccess} />
      )}

      <div className="min-h-[80vh] flex items-center justify-center px-4 py-12">
        <div className="w-full max-w-md">
          <div className="bg-white rounded-2xl shadow-xl border border-border overflow-hidden">
            <div className="bg-gradient-to-r from-primary to-accent px-8 py-6 text-white text-center">
              <div className="flex justify-center mb-3">
                <div className="bg-white/20 rounded-full p-3">
                  <Heart className="w-7 h-7 text-white" />
                </div>
              </div>
              <h1 className="text-2xl font-bold">Create Account</h1>
              <p className="text-white/80 text-sm mt-1">Join the Rural Healthcare System</p>
            </div>

            <div className="px-8 py-8 space-y-5">
              {error && (
                <div className="flex items-start gap-3 p-3 bg-red-50 border border-red-200 rounded-lg">
                  <AlertCircle className="w-5 h-5 text-red-500 flex-shrink-0 mt-0.5" />
                  <p className="text-red-700 text-sm">{error}</p>
                </div>
              )}
              {success && (
                <div className="flex items-start gap-3 p-3 bg-green-50 border border-green-200 rounded-lg">
                  <CheckCircle2 className="w-5 h-5 text-green-500 flex-shrink-0 mt-0.5" />
                  <p className="text-green-700 text-sm">{success}</p>
                </div>
              )}

              <form onSubmit={handleSubmit} className="space-y-4">
                <div>
                  <label className="block text-sm font-medium text-foreground mb-1.5" htmlFor="fullName">Full Name</label>
                  <div className="relative">
                    <User className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
                    <input id="fullName" ref={nameRef} type="text" name="fullName" placeholder="Your full name" value={formData.fullName} onChange={handleChange} onBlur={handleFieldBlur}
                      className={`w-full pl-10 pr-4 py-2.5 border rounded-lg text-sm focus:outline-none focus:ring-2 transition ${fieldClass("fullName")}`} required />
                  </div>
                  {showFieldError("fullName") && <p className="text-red-500 text-xs mt-1">{nameError}</p>}
                </div>

                <div>
                  <label className="block text-sm font-medium text-foreground mb-1.5" htmlFor="reg-email">Email Address</label>
                  <div className="relative">
                    <Mail className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
                    <input id="reg-email" ref={emailRef} type="email" name="email" placeholder="you@example.com" value={formData.email} onChange={handleChange} onBlur={handleFieldBlur}
                      pattern="[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}"
                      title="Enter a valid email address"
                      className={`w-full pl-10 pr-4 py-2.5 border rounded-lg text-sm focus:outline-none focus:ring-2 transition ${fieldClass("email")}`} required />
                  </div>
                  {showFieldError("email") && <p className="text-red-500 text-xs mt-1">{emailError}</p>}
                </div>

                <div>
                  <label className="block text-sm font-medium text-foreground mb-1.5" htmlFor="reg-password">
                    Password <span className="text-muted-foreground font-normal">(min. 12 characters)</span>
                  </label>
                  <div className="relative">
                    <Lock className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
                    <input id="reg-password" ref={passwordRef} type={showPassword ? "text" : "password"} name="password" placeholder="Create a strong password" value={formData.password} onChange={handleChange} onBlur={handleFieldBlur}
                      className={`w-full pl-10 pr-10 py-2.5 border rounded-lg text-sm focus:outline-none focus:ring-2 transition ${fieldClass("password")}`} required />
                    <button type="button" onClick={() => setShowPassword((v) => !v)} className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground transition" tabIndex={-1}>
                      {showPassword ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                    </button>
                  </div>
                  {strength && (
                    <div className="mt-2">
                      <div className="flex items-center gap-2">
                        <span className="text-xs text-muted-foreground">Strength:</span>
                        <div className="flex-1 h-1.5 bg-border rounded-full overflow-hidden">
                          <div className={`h-full rounded-full transition-all duration-300 ${STRENGTH_BAR[strength.score]}`} style={{ width: `${(strength.score + 1) * 20}%` }} />
                        </div>
                        <span className={`text-xs font-medium ${STRENGTH_TEXT[strength.score]}`}>{strength.label}</span>
                      </div>
                      <ul className="grid grid-cols-1 gap-1 pt-2">
                        {checklist.map((rule) => (
                          <li key={rule.key} className={`text-xs flex items-center gap-1.5 ${rule.met ? "text-green-600" : "text-muted-foreground"}`}>
                            {rule.met ? <Check className="w-3.5 h-3.5 flex-shrink-0" /> : <Circle className="w-3 h-3 flex-shrink-0" />}
                            {rule.label}
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                  {showFieldError("password") && <p className="text-red-500 text-xs mt-1">{passwordError}</p>}
                </div>

                <div>
                  <label className="block text-sm font-medium text-foreground mb-1.5" htmlFor="confirmPassword">Confirm Password</label>
                  <div className="relative">
                    <Lock className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
                    <input id="confirmPassword" ref={confirmRef} type={showConfirm ? "text" : "password"} name="confirmPassword" placeholder="Re-enter your password" value={formData.confirmPassword} onChange={handleChange} onBlur={handleFieldBlur}
                      className={`w-full pl-10 pr-10 py-2.5 border rounded-lg text-sm focus:outline-none focus:ring-2 transition ${fieldClass("confirmPassword")}`} required />
                    <button type="button" onClick={() => setShowConfirm((v) => !v)} className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground transition" tabIndex={-1}>
                      {showConfirm ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                    </button>
                  </div>
                  {showFieldError("confirmPassword") && <p className="text-red-500 text-xs mt-1">{confirmError}</p>}
                </div>

                <button type="submit"
                  disabled={loading || hasErrors}
                  className="w-full py-2.5 bg-primary text-primary-foreground font-semibold rounded-lg hover:bg-primary/90 transition disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-2">
                  {loading ? (<><Loader2 className="w-4 h-4 animate-spin" />Creating Account...</>) : "Create Account"}
                </button>
              </form>

              <div className="flex items-center gap-3">
                <div className="flex-1 h-px bg-border" />
                <span className="text-xs text-muted-foreground">OR</span>
                <div className="flex-1 h-px bg-border" />
              </div>

              <button type="button" onClick={() => handleOAuth("google")}
                className="w-full flex items-center justify-center gap-3 px-4 py-2.5 border border-gray-300 rounded-lg bg-white hover:bg-gray-50 shadow-sm transition text-gray-700 font-medium text-sm">
                <GoogleIcon />
                Sign up with Google
              </button>

              <button type="button" onClick={() => handleOAuth("github")}
                className="w-full flex items-center justify-center gap-3 px-4 py-2.5 border border-transparent rounded-lg bg-[#24292e] hover:bg-[#2f363d] shadow-sm transition text-white font-medium text-sm">
                <GitHubIcon />
                Sign up with GitHub
              </button>

              <p className="text-center text-sm text-muted-foreground">
                Already have an account? <Link to="/login" className="text-primary font-semibold hover:underline">Sign in</Link>
              </p>
            </div>
          </div>
        </div>
      </div>
    </Layout>
  );
}
