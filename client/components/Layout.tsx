import { useState, useEffect, useRef } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { X, Mail, LogOut, User, ClipboardList, ChevronDown, Star } from "lucide-react";
import { sendFeedbackEmail } from "../services/emailService";
import { useAuth } from "@/context/AuthContext";

interface LayoutProps {
  children: React.ReactNode;
}

interface FeedbackForm {
  name: string;
  email: string;
  subject: string;
  message: string;
  // 1-5, or 0 when the user hasn't picked a rating -- optional, so leaving
  // it unset never blocks submitting a text-only feedback message.
  rating: number;
}

export const Layout = ({ children }: LayoutProps) => {
  const { user, signOut } = useAuth();
  const [showFeedback, setShowFeedback] = useState(false);
  const [form, setForm] = useState<FeedbackForm>({ name: "", email: "", subject: "", message: "", rating: 0 });
  // Star currently under the pointer, so the row can preview up to that
  // star (hover state) without committing until clicked.
  const [hoverRating, setHoverRating] = useState(0);
  const [currentUser, setCurrentUser] = useState<{ fullName?: string; name?: string; email: string } | null>(null);
  const [showProfileMenu, setShowProfileMenu] = useState(false);
  const profileMenuRef = useRef<HTMLDivElement>(null);
  const navigate = useNavigate();
  const location = useLocation();
  const isHomePage = location.pathname === "/";

  useEffect(() => {
    if (user) {
      const name = (user.user_metadata?.full_name as string) || user.email?.split("@")[0] || "";
      setCurrentUser({ fullName: name, email: user.email || "" });
      if (user.email) {
        localStorage.setItem("user", JSON.stringify({ fullName: name, email: user.email, googleAuth: true }));
      }
    } else {
      setCurrentUser(null);
      localStorage.removeItem("user");
    }
  }, [user]);

  // Lets any page open the shared feedback modal (it lives here in Layout,
  // rendered once for every route) without prop-drilling or a new context --
  // a page just links/navigates to `?feedback=open` on its own path (see the
  // Contact Us page's Feedback button) and this effect opens the modal, then
  // strips the query param so it doesn't linger in the URL/history.
  useEffect(() => {
    if (new URLSearchParams(location.search).get("feedback") === "open") {
      setShowFeedback(true);
      navigate(location.pathname, { replace: true });
    }
  }, [location.search, location.pathname, navigate]);

  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (profileMenuRef.current && !profileMenuRef.current.contains(e.target as Node)) {
        setShowProfileMenu(false);
      }
    };
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  const handleLogout = async () => {
    await signOut();
    setCurrentUser(null);
    setShowProfileMenu(false);
    navigate("/");
  };

  const displayName = currentUser?.fullName || currentUser?.name || currentUser?.email || "";
  const initials = displayName
    .split(" ")
    .map((n) => n[0])
    .join("")
    .toUpperCase()
    .slice(0, 2);

  const handleChange = (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>) => {
    setForm((prev) => ({ ...prev, [e.target.name]: e.target.value }));
  };

  const [feedbackStatus, setFeedbackStatus] = useState<string | null>(null);
  const [feedbackLoading, setFeedbackLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setFeedbackStatus(null);
    setFeedbackLoading(true);

    if (!form.message || !form.message.trim()) {
      setFeedbackStatus("Please enter a message.");
      setFeedbackLoading(false);
      return;
    }

    try {
      const result = await sendFeedbackEmail({
        name: form.name,
        email: form.email,
        message: form.message,
        rating: form.rating || undefined,
      });
      if (result?.success) {
        setFeedbackStatus("Feedback sent successfully!");
        setForm({ name: "", email: "", subject: "", message: "", rating: 0 });
        setTimeout(() => {
          setShowFeedback(false);
          setFeedbackStatus(null);
        }, 2000);
      } else {
        setFeedbackStatus(result?.message || "Failed to send feedback.");
      }
    } catch (err) {
      setFeedbackStatus("Could not send feedback. Please try again later.");
    } finally {
      setFeedbackLoading(false);
    }
  };

  return (
    <div className="min-h-screen flex flex-col bg-background text-foreground">
      {/* Feedback Modal */}
      {showFeedback && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
          <div className="bg-white rounded-xl shadow-2xl w-full max-w-lg relative">
            <button
              onClick={() => setShowFeedback(false)}
              className="absolute top-4 right-4 text-muted-foreground hover:text-foreground transition-colors"
              aria-label="Close feedback form"
            >
              <X className="w-5 h-5" />
            </button>
            <div className="p-6">
              <div className="flex items-center gap-3 mb-4">
                <div className="bg-primary/10 p-2 rounded-lg">
                  <Mail className="w-5 h-5 text-primary" />
                </div>
                <div>
                  <h2 className="text-lg font-semibold text-foreground">Send Us Feedback</h2>
                  <p className="text-xs text-muted-foreground">
                    Prefer Gmail? <a
                      href="https://mail.google.com/mail/?view=cm&fs=1&to=ruralhealthcareai@gmail.com&su=Feedback%20%E2%80%93%20Rural%20Healthcare%20System"
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-primary hover:underline"
                    >Click here to open Gmail compose</a>
                  </p>
                </div>
              </div>
              <form onSubmit={handleSubmit} className="space-y-4">
                <div>
                  <label className="block text-sm font-medium text-foreground mb-1" htmlFor="fb-name">
                    Your Name <span className="text-red-500">*</span>
                  </label>
                  <input
                    id="fb-name"
                    name="name"
                    type="text"
                    value={form.name}
                    onChange={handleChange}
                    required
                    placeholder="John Doe"
                    className="w-full px-3 py-2 border border-border rounded-md text-sm focus:outline-none"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-foreground mb-1" htmlFor="fb-email">
                    Your Email <span className="text-red-500">*</span>
                  </label>
                  <input
                    id="fb-email"
                    name="email"
                    type="email"
                    value={form.email}
                    onChange={handleChange}
                    required
                    placeholder="you@example.com"
                    className="w-full px-3 py-2 border border-border rounded-md text-sm focus:outline-none"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-foreground mb-1">
                    Your Rating <span className="text-xs font-normal text-muted-foreground">(optional)</span>
                  </label>
                  <div
                    className="flex items-center gap-1"
                    onMouseLeave={() => setHoverRating(0)}
                  >
                    {[1, 2, 3, 4, 5].map((star) => {
                      const filled = star <= (hoverRating || form.rating);
                      return (
                        <button
                          key={star}
                          type="button"
                          onClick={() =>
                            setForm((prev) => ({ ...prev, rating: prev.rating === star ? 0 : star }))
                          }
                          onMouseEnter={() => setHoverRating(star)}
                          aria-label={`Rate ${star} out of 5 star${star > 1 ? "s" : ""}`}
                          aria-pressed={form.rating === star}
                          className="p-0.5 text-amber-400 transition-transform hover:scale-110 focus:outline-none"
                        >
                          <Star className={`w-6 h-6 ${filled ? "fill-current" : "fill-none"}`} />
                        </button>
                      );
                    })}
                    {form.rating > 0 && (
                      <span className="ml-2 text-xs text-muted-foreground">{form.rating}/5</span>
                    )}
                  </div>
                </div>
                <div>
                  <textarea
                    name="message"
                    rows={4}
                    required
                    value={form.message}
                    onChange={handleChange}
                    placeholder="Write your feedback here..."
                    className="w-full px-3 py-2 border border-border rounded-md text-sm focus:outline-none resize-vertical"
                  />
                </div>
                <div className="flex items-center justify-between gap-2">
                  <p className="text-xs text-muted-foreground">Your feedback will be sent securely to our team.</p>
                  <button
                    type="submit"
                    disabled={feedbackLoading}
                    className={`px-4 py-2 rounded-md text-sm font-medium ${feedbackLoading ? 'bg-gray-400 text-white' : 'bg-primary text-primary-foreground'}`}>
                    {feedbackLoading ? 'Sending...' : 'Send Feedback'}
                  </button>
                </div>
                {feedbackStatus && (
                  <div className={`mt-2 text-sm ${feedbackStatus?.toLowerCase().includes('success') ? 'text-green-600' : 'text-red-600'}`}>{feedbackStatus}</div>
                )}
              </form>
            </div>
          </div>
        </div>
      )}

      <header className="bg-white border-b border-border shadow-sm">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-4 flex items-center justify-between">
          <Link to="/" className="flex items-center gap-3 hover:opacity-80 transition-opacity">
            <img
              src="/Logo img.jpg"
              alt="Rural Healthcare Logo"
              className="w-12 h-12 rounded-lg object-cover"
            />
            <div className="flex flex-col">
              <h1 className="font-bold text-lg text-primary">Rural Healthcare</h1>
              <p className="text-xs text-muted-foreground">AI-Powered System</p>
            </div>
          </Link>
          
          <nav className="hidden sm:flex items-center gap-8">
            <div className="flex items-center gap-8">
              <Link
                to="/"
                className="text-sm font-medium text-foreground hover:text-primary transition-colors"
              >
                Home
              </Link>
              <Link
                to="/assess"
                className="text-sm font-medium text-foreground hover:text-primary transition-colors"
              >
                Health Assessment
              </Link>
              <Link
                to="/ai-assistant"
                className="text-sm font-medium text-foreground hover:text-primary transition-colors"
              >
                AI Assistant
              </Link>
              <Link
                to="/admin"
                className="text-sm font-medium text-foreground hover:text-primary transition-colors"
              >
                Admin
              </Link>
              <Link
                to="/doctor"
                className="text-sm font-medium text-foreground hover:text-primary transition-colors"
              >
                Doctor
              </Link>
            </div>

            <div className="ml-6 flex items-center gap-4">
              {currentUser ? (
                <div className="relative" ref={profileMenuRef}>
                  <button
                    onClick={() => setShowProfileMenu((v) => !v)}
                    className="flex items-center gap-2 px-3 py-1.5 rounded-full bg-primary/10 hover:bg-primary/20 transition-colors focus:outline-none"
                    aria-label="Profile menu"
                  >
                    <div className="w-8 h-8 rounded-full bg-primary flex items-center justify-center text-white text-xs font-bold flex-shrink-0">
                      {initials || <User className="w-4 h-4" />}
                    </div>
                    <span className="text-sm font-medium text-foreground max-w-[120px] truncate hidden sm:block">
                      {displayName}
                    </span>
                    <ChevronDown className={`w-4 h-4 text-muted-foreground transition-transform ${showProfileMenu ? "rotate-180" : ""}`} />
                  </button>

                  {showProfileMenu && (
                    <div className="absolute right-0 mt-2 w-64 bg-white border border-border rounded-xl shadow-xl z-50 overflow-hidden">
                      {/* User info header */}
                      <div className="px-4 py-3 bg-primary/5 border-b border-border">
                        <div className="flex items-center gap-3">
                          <div className="w-10 h-10 rounded-full bg-primary flex items-center justify-center text-white text-sm font-bold flex-shrink-0">
                            {initials || <User className="w-4 h-4" />}
                          </div>
                          <div className="min-w-0">
                            <p className="text-sm font-semibold text-foreground truncate">{displayName}</p>
                            <p className="text-xs text-muted-foreground truncate">{currentUser.email}</p>
                          </div>
                        </div>
                      </div>
                      {/* Menu items */}
                      <div className="py-1">
                        <Link
                          to="/profile"
                          onClick={() => setShowProfileMenu(false)}
                          className="flex items-center gap-3 px-4 py-2.5 text-sm text-foreground hover:bg-primary/5 transition-colors"
                        >
                          <User className="w-4 h-4 text-primary" />
                          My Profile
                        </Link>
                        <Link
                          to="/profile#assessments"
                          onClick={() => setShowProfileMenu(false)}
                          className="flex items-center gap-3 px-4 py-2.5 text-sm text-foreground hover:bg-primary/5 transition-colors"
                        >
                          <ClipboardList className="w-4 h-4 text-primary" />
                          My Assessments
                        </Link>
                        <div className="border-t border-border mt-1 pt-1">
                          <button
                            onClick={handleLogout}
                            className="flex items-center gap-3 w-full px-4 py-2.5 text-sm text-red-600 hover:bg-red-50 transition-colors"
                          >
                            <LogOut className="w-4 h-4" />
                            Logout
                          </button>
                        </div>
                      </div>
                    </div>
                  )}
                </div>
              ) : (
                <>
                  <Link
                    to="/login"
                    className="text-sm font-medium text-foreground hover:text-primary transition-colors"
                  >
                    Login
                  </Link>
                  <Link
                    to="/register"
                    className="text-sm font-medium px-3 py-2 bg-primary text-primary-foreground rounded-md hover:bg-primary/90 transition-colors"
                  >
                    Register
                  </Link>
                </>
              )}
            </div>
          </nav>
        </div>
      </header>

      <main className="flex-1 w-full">
        {children}
      </main>

      {isHomePage && (
        <footer className="bg-gray-50 border-t border-border mt-12">
          <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
            <div className="grid grid-cols-1 md:grid-cols-3 gap-8">
              <div>
                <h3 className="font-semibold text-foreground mb-2">About</h3>
                <p className="text-sm text-muted-foreground">
                  AI-powered healthcare assistance for rural communities.
                </p>
              </div>
              <div>
                <h3 className="font-semibold text-foreground mb-2">Support</h3>
                <ul className="text-sm text-muted-foreground space-y-1">
                  <li><Link to="/contact-us" className="hover:text-primary transition-colors">Contact Us</Link></li>
                  <li>Phone: +91 1234567896</li>
                  <li>Email:{" "}
                    <a
                      href="https://mail.google.com/mail/?view=cm&fs=1&to=ruralhealthcareai@gmail.com&su=Feedback%20%E2%80%93%20Rural%20Healthcare%20System"
                      target="_blank"
                      rel="noopener noreferrer"
                      className="hover:text-primary transition-colors underline underline-offset-2 text-left"
                    >
                      ruralhealthcareai@gmail.com
                    </a>
                    <span className="ml-2">or <button onClick={() => setShowFeedback(true)} className="hover:text-primary transition-colors underline underline-offset-2 text-left">Feedback</button></span>
                  </li>
                </ul>
              </div>
              <div>
                <h3 className="font-semibold text-foreground mb-2">Legal</h3>
                <ul className="text-sm text-muted-foreground space-y-1">
                  <li><Link to="/privacy-policy" className="hover:text-primary transition-colors">Privacy Policy</Link></li>
                  <li><Link to="/terms-of-service" className="hover:text-primary transition-colors">Terms of Service</Link></li>
                  <li><Link to="/disclaimer" className="hover:text-primary transition-colors">Disclaimer</Link></li>
                </ul>
              </div>
            </div>
            <div className="border-t border-border mt-8 pt-8 text-center text-sm text-muted-foreground">
              <p>&copy; 2026 Rural Healthcare System. All rights reserved.</p>
            </div>
          </div>
        </footer>
      )}
    </div>
  );
};
