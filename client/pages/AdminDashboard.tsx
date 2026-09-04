import { Layout } from "@/components/Layout";
import { ConfirmDeleteModal } from "@/components/ConfirmDeleteModal";
import { useEffect, useMemo, useRef, useState } from "react";
import { Activity, AlertTriangle, ClipboardList, Loader2, Lock, LogOut, Search, ShieldCheck, Star, Trash2, Users } from "lucide-react";
import { formatIST } from "@/lib/formatDate";

const BACKEND = import.meta.env.VITE_BACKEND_URL || "http://127.0.0.1:5001";
const SESSION_KEY = "admin_auth";
const SESSION_EMAIL_KEY = "admin_auth_email";

type PatientRecord = {
  id: number;
  createdAt: string;
  email?: string;
  patientName: string;
  age: string;
  bloodPressureSystolic: string;
  bloodPressureDiastolic: string;
  heartRate: string;
  temperature: string;
  sugarLevel: string;
  labTestResult: string;
  symptoms: string[];
  medicalReportName: string;
  predictedDisease: string;
  confidence: number;
  // riskLevel ("Low Risk" | "Medium Risk" | "High Risk") is the AI risk
  // classification -- disease clinical severity x model confidence (see
  // backend/risk_classification.py). riskCategory ("Low" | "Moderate" |
  // "High") is a SEPARATE, vitals-only score (BP/HR/temp/sugar/age --
  // see analyze_vitals() in app.py) that ignores the predicted disease
  // and confidence entirely. The two can legitimately disagree (e.g. a
  // 96%-confidence high-severity disease with otherwise normal vitals),
  // which is why the dashboard's primary risk badge must show riskLevel,
  // not riskCategory.
  riskLevel: string;
  riskCategory: string;
  riskScore: number;
  recommendation: string;
};

// Shape returned by GET /api/users -- read live from Supabase Auth
// (backend/supabase_admin.py), not a local database mirror.
type RegisteredUser = {
  id: string;
  email: string;
  full_name: string;
  provider: string;
  created_at: string | null;
  last_sign_in_at: string | null;
};

// Shape returned by GET /api/feedback -- one row per "send via form"
// submission on the home page footer (see Layout.tsx's feedback modal).
type FeedbackEntry = {
  id: number;
  createdAt: string;
  name: string;
  email: string;
  subject: string;
  message: string;
  rating: number | null; // 1-5, or null when the user didn't rate
  delivery: string;
  deliveryStatus: string;
};

// Shape returned by GET /api/admin-logs -- one row per login attempt or
// destructive patient action taken from this dashboard (see admin_data in
// backend/mysql_store.py).
type AdminLogEntry = {
  id: number;
  created_at: string;
  actor_email: string;
  action: string;
  target_type: string;
  target_id: string;
  details: string;
  ip_address: string;
};

function AdminLoginGate({ onAuth }: { onAuth: () => void }) {
  const [email, setEmail]       = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError]       = useState("");
  const emailRef = useRef<HTMLInputElement>(null);

  useEffect(() => { emailRef.current?.focus(); }, []);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!email.trim() || !password.trim()) {
      setError("Both fields are required.");
      return;
    }
    setSubmitting(true);
    setError("");
    try {
      const resp = await fetch(`${BACKEND}/admin-login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: email.trim(), password }),
      });
      const data = await resp.json();
      if (data.success) {
        sessionStorage.setItem(SESSION_KEY, "true");
        sessionStorage.setItem(SESSION_EMAIL_KEY, email.trim());
        onAuth();
      } else {
        setError(data.error || "Invalid credentials.");
      }
    } catch {
      setError("Cannot connect to backend. Make sure the server is running.");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Layout>
      <div className="min-h-screen bg-gradient-to-br from-slate-50 via-emerald-50/40 to-cyan-50/50 flex items-center justify-center px-4">
        <div className="w-full max-w-md">
          {/* Card */}
          <div className="bg-white border border-border rounded-2xl shadow-lg overflow-hidden">
            {/* Header */}
            <div className="bg-gradient-to-r from-primary to-emerald-600 px-8 py-7 text-white text-center">
              <div className="flex justify-center mb-3">
                <div className="bg-white/20 rounded-full p-3">
                  <ShieldCheck className="w-8 h-8" />
                </div>
              </div>
              <h1 className="text-2xl font-bold">Admin Access</h1>
              <p className="text-sm text-white/80 mt-1">Sign in to view the patient dashboard</p>
            </div>

            {/* Form */}
            <form onSubmit={handleSubmit} className="px-8 py-7 space-y-5">
              {error && (
                <div className="bg-red-50 border border-red-200 text-red-700 text-sm rounded-lg px-4 py-3">
                  {error}
                </div>
              )}

              <div className="space-y-1.5">
                <label className="block text-sm font-medium text-foreground">Admin Email</label>
                <input
                  ref={emailRef}
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="admin@ruralhealthcare.com"
                  className="w-full border border-border rounded-lg px-4 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-primary/40 focus:border-primary transition"
                  required
                />
              </div>

              <div className="space-y-1.5">
                <label className="block text-sm font-medium text-foreground">Password</label>
                <input
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="Enter admin password"
                  className="w-full border border-border rounded-lg px-4 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-primary/40 focus:border-primary transition"
                  required
                />
              </div>

              <button
                type="submit"
                disabled={submitting}
                className="w-full flex items-center justify-center gap-2 bg-primary text-primary-foreground rounded-lg px-4 py-2.5 text-sm font-semibold hover:bg-primary/90 disabled:opacity-60 transition"
              >
                {submitting ? <Loader2 className="w-4 h-4 animate-spin" /> : <Lock className="w-4 h-4" />}
                {submitting ? "Verifying…" : "Sign In"}
              </button>
            </form>
          </div>
          <p className="text-center text-xs text-muted-foreground mt-4">
            Restricted area — authorised personnel only.
          </p>
        </div>
      </div>
    </Layout>
  );
}

export default function AdminDashboard() {
  const [authenticated, setAuthenticated] = useState(
    () => sessionStorage.getItem(SESSION_KEY) === "true"
  );
  const [records, setRecords] = useState<PatientRecord[]>([]);
  const [loading, setLoading] = useState(false);
  const [deletingId, setDeletingId] = useState<number | null>(null);
  const [deletingAll, setDeletingAll] = useState(false);
  // Drive the in-app ConfirmDeleteModal (replaces window.confirm()):
  // deleteTarget holds the id of the row pending confirmation, confirmDeleteAll
  // is set when "Delete All" is clicked. Only one of these is ever open at a time.
  const [deleteTarget, setDeleteTarget] = useState<number | null>(null);
  const [confirmDeleteAll, setConfirmDeleteAll] = useState(false);
  const [query, setQuery] = useState("");
  const [riskFilter, setRiskFilter] = useState<"" | "High Risk" | "Medium Risk" | "Low Risk">("")
  const [error, setError] = useState("");
  const [tab, setTab] = useState<"patients" | "users" | "feedback" | "activity">("patients");
  const [users, setUsers] = useState<RegisteredUser[]>([]);
  const [usersLoading, setUsersLoading] = useState(false);
  const [usersError, setUsersError] = useState("");
  const [feedback, setFeedback] = useState<FeedbackEntry[]>([]);
  const [feedbackLoading, setFeedbackLoading] = useState(false);
  const [feedbackError, setFeedbackError] = useState("");
  const [logs, setLogs] = useState<AdminLogEntry[]>([]);
  const [logsLoading, setLogsLoading] = useState(false);
  const [logsError, setLogsError] = useState("");

  // Sent as X-Actor-Email/X-Actor-Role on delete calls below so
  // admin_data (backend/mysql_store.py) can attribute the action to this
  // admin instead of logging it as an anonymous "admin" action.
  const actorHeaders = (): Record<string, string> => ({
    "X-Actor-Email": sessionStorage.getItem(SESSION_EMAIL_KEY) || "",
    "X-Actor-Role": "admin",
  });

  const logout = () => {
    sessionStorage.removeItem(SESSION_KEY);
    sessionStorage.removeItem(SESSION_EMAIL_KEY);
    setAuthenticated(false);
  };

  const fetchPatients = async () => {
    setLoading(true);
    setError("");
    try {
      const response = await fetch(`${BACKEND}/patients`);
      const data = await response.json();
      if (data.success) {
        setRecords(data.patients || []);
      } else {
        setError("Failed to load patient records.");
      }
    } catch {
      setError("Unable to connect to backend. Please ensure it is running at http://127.0.0.1:5001");
    } finally {
      setLoading(false);
    }
  };

  const fetchUsers = async () => {
    setUsersLoading(true);
    setUsersError("");
    try {
      const response = await fetch(`${BACKEND}/api/users`);
      const data = await response.json();
      if (data.success) {
        setUsers(data.users || []);
      } else {
        setUsersError("Failed to load registered users.");
      }
    } catch {
      setUsersError("Unable to connect to backend. Please ensure it is running at http://127.0.0.1:5001");
    } finally {
      setUsersLoading(false);
    }
  };

  const fetchFeedback = async () => {
    setFeedbackLoading(true);
    setFeedbackError("");
    try {
      const response = await fetch(`${BACKEND}/api/feedback`);
      const data = await response.json();
      if (data.success) {
        setFeedback(data.feedback || []);
      } else {
        setFeedbackError("Failed to load feedback.");
      }
    } catch {
      setFeedbackError("Unable to connect to backend. Please ensure it is running at http://127.0.0.1:5001");
    } finally {
      setFeedbackLoading(false);
    }
  };

  const fetchLogs = async () => {
    setLogsLoading(true);
    setLogsError("");
    try {
      const response = await fetch(`${BACKEND}/api/admin-logs`);
      const data = await response.json();
      if (data.success) {
        setLogs(data.logs || []);
      } else {
        setLogsError("Failed to load activity log.");
      }
    } catch {
      setLogsError("Unable to connect to backend. Please ensure it is running at http://127.0.0.1:5001");
    } finally {
      setLogsLoading(false);
    }
  };

  useEffect(() => {
    if (!authenticated) return;
    fetchPatients();
    fetchUsers();
    fetchFeedback();
    fetchLogs();
  }, [authenticated]);

  // Confirmation now happens via the in-app ConfirmDeleteModal (see JSX
  // below) before either of these is ever called -- neither function
  // prompts on its own anymore.
  const handleDelete = async (id: number) => {
    setDeletingId(id);
    setError("");
    try {
      const response = await fetch(`${BACKEND}/patients/${id}`, { method: "DELETE", headers: actorHeaders() });
      const data = await response.json();
      if (!response.ok || !data.success) {
        setError(data.error || "Failed to delete patient record.");
        return;
      }
      setRecords((prev) => prev.filter((record) => record.id !== id));
      fetchLogs();
    } catch {
      setError("Unable to connect to backend. Please ensure it is running at http://127.0.0.1:5001");
    } finally {
      setDeletingId(null);
      setDeleteTarget(null);
    }
  };

  const handleDeleteAll = async () => {
    setDeletingAll(true);
    setError("");
    try {
      const response = await fetch(`${BACKEND}/patients/delete/all`, { method: "DELETE", headers: actorHeaders() });
      const data = await response.json();
      if (!response.ok || !data.success) {
        setError(data.error || "Failed to delete all patient records.");
        return;
      }
      setRecords([]);
      setRiskFilter("");
      fetchLogs();
    } catch {
      setError("Unable to connect to backend. Please ensure it is running at http://127.0.0.1:5001");
    } finally {
      setDeletingAll(false);
      setConfirmDeleteAll(false);
    }
  };

  const stats = useMemo(() => {
    const total = records.length;
    const highRisk = records.filter((r) => r.riskLevel === "High Risk").length;
    const moderateRisk = records.filter((r) => r.riskLevel === "Medium Risk").length;
    const avgConfidence = total
      ? Math.round(records.reduce((sum, r) => sum + Number(r.confidence || 0), 0) / total)
      : 0;
    return { total, highRisk, moderateRisk, avgConfidence };
  }, [records]);

  const filteredRecords = useMemo(() => {
    let filtered = records;

    // Filter by AI risk level if selected
    if (riskFilter) {
      filtered = filtered.filter((r) => r.riskLevel === riskFilter);
    }

    // Filter by search query
    const q = query.trim().toLowerCase();
    if (q) {
      filtered = filtered.filter((record) => {
        const searchable = [
          String(record.id),
          record.patientName,
          record.age,
          record.predictedDisease,
          record.riskLevel,
          record.riskCategory,
          String(record.riskScore),
          String(record.confidence),
          (record.symptoms || []).join(" "),
          record.createdAt,
        ]
          .join(" ")
          .toLowerCase();
        return searchable.includes(q);
      });
    }

    return filtered;
  }, [records, query, riskFilter]);

  if (!authenticated) {
    return <AdminLoginGate onAuth={() => setAuthenticated(true)} />;
  }

  return (
    <Layout>
      <div className="min-h-screen bg-gradient-to-br from-slate-50 via-emerald-50/40 to-cyan-50/50 py-8">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 mb-6">
            <div>
              <h1 className="text-3xl font-bold text-foreground">Admin Dashboard</h1>
              <p className="text-muted-foreground">All patient assessments submitted through the application</p>
            </div>
            <div className="flex flex-col sm:flex-row gap-2 sm:items-center">
              <div className="relative">
                <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" />
                <input
                  type="text"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder="Search by name, disease, symptom, id"
                  className="w-full sm:w-80 border border-border rounded-lg pl-9 pr-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary/40 focus:border-primary transition"
                />
              </div>
              <button
                onClick={fetchPatients}
                className="px-4 py-2 rounded-lg bg-primary text-primary-foreground text-sm font-medium hover:bg-primary/90 transition-colors"
              >
                Refresh Data
              </button>
              {records.length > 0 && (
                <button
                  onClick={() => setConfirmDeleteAll(true)}
                  disabled={deletingAll}
                  className="flex items-center gap-1.5 px-4 py-2 rounded-lg bg-red-600 text-white text-sm font-medium hover:bg-red-700 disabled:opacity-60 transition-colors"
                >
                  {deletingAll ? <Loader2 className="w-4 h-4 animate-spin" /> : <Trash2 className="w-4 h-4" />}
                  Delete All
                </button>
              )}
              <button
                onClick={logout}
                className="flex items-center gap-1.5 px-4 py-2 rounded-lg border border-border text-sm font-medium text-muted-foreground hover:text-red-600 hover:border-red-300 transition-colors"
              >
                <LogOut className="w-4 h-4" />
                Logout
              </button>
            </div>
          </div>

          <div className="flex gap-2 mb-6">
            <button
              onClick={() => setTab("patients")}
              className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
                tab === "patients"
                  ? "bg-primary text-primary-foreground"
                  : "bg-white border border-border text-muted-foreground hover:text-foreground"
              }`}
            >
              Patient Assessments
            </button>
            <button
              onClick={() => setTab("users")}
              className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
                tab === "users"
                  ? "bg-primary text-primary-foreground"
                  : "bg-white border border-border text-muted-foreground hover:text-foreground"
              }`}
            >
              Registered Users
            </button>
            <button
              onClick={() => setTab("feedback")}
              className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
                tab === "feedback"
                  ? "bg-primary text-primary-foreground"
                  : "bg-white border border-border text-muted-foreground hover:text-foreground"
              }`}
            >
              Feedback
            </button>
            <button
              onClick={() => setTab("activity")}
              className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
                tab === "activity"
                  ? "bg-primary text-primary-foreground"
                  : "bg-white border border-border text-muted-foreground hover:text-foreground"
              }`}
            >
              Activity Log
            </button>
          </div>

          {tab === "activity" ? (
            <div className="bg-white rounded-xl border border-border shadow-sm overflow-hidden">
              <div className="px-4 py-3 flex items-center justify-between border-b border-border bg-gray-50">
                <p className="text-sm text-muted-foreground">
                  {logsLoading ? "Loading…" : `${logs.length} recent event(s)`}
                </p>
                <button
                  onClick={fetchLogs}
                  className="px-3 py-1.5 rounded-lg bg-primary text-primary-foreground text-xs font-medium hover:bg-primary/90 transition-colors"
                >
                  Refresh
                </button>
              </div>
              {logsLoading ? (
                <div className="p-8 flex items-center justify-center gap-2 text-muted-foreground">
                  <Loader2 className="w-5 h-5 animate-spin" />
                  Loading activity log...
                </div>
              ) : logsError ? (
                <div className="p-4 text-red-700 bg-red-50">{logsError}</div>
              ) : logs.length === 0 ? (
                <div className="p-8 text-center text-muted-foreground">
                  No admin activity recorded yet. Logins and patient deletions will show up here.
                </div>
              ) : (
                <div className="overflow-x-auto">
                  <table className="min-w-full text-sm">
                    <thead className="bg-gray-50 border-b border-border">
                      <tr>
                        <th className="px-4 py-3 text-left font-semibold text-foreground whitespace-nowrap">When</th>
                        <th className="px-4 py-3 text-left font-semibold text-foreground whitespace-nowrap">Admin</th>
                        <th className="px-4 py-3 text-left font-semibold text-foreground whitespace-nowrap">Action</th>
                        <th className="px-4 py-3 text-left font-semibold text-foreground whitespace-nowrap">Target</th>
                        <th className="px-4 py-3 text-left font-semibold text-foreground whitespace-nowrap">Details</th>
                        <th className="px-4 py-3 text-left font-semibold text-foreground whitespace-nowrap">IP</th>
                      </tr>
                    </thead>
                    <tbody>
                      {logs.map((log) => (
                        <tr key={log.id} className="border-b border-border align-top">
                          <td className="px-4 py-3 text-muted-foreground whitespace-nowrap">
                            {formatIST(log.created_at)}
                          </td>
                          <td className="px-4 py-3 text-foreground whitespace-nowrap">{log.actor_email || "-"}</td>
                          <td className="px-4 py-3 whitespace-nowrap">
                            <span
                              className={`inline-flex items-center px-2.5 py-1 rounded-full text-xs font-semibold ${
                                log.action === "login_failed"
                                  ? "bg-red-100 text-red-700"
                                  : log.action.startsWith("delete")
                                    ? "bg-amber-100 text-amber-700"
                                    : "bg-emerald-100 text-emerald-700"
                              }`}
                            >
                              {log.action}
                            </span>
                          </td>
                          <td className="px-4 py-3 text-muted-foreground whitespace-nowrap">
                            {log.target_type ? `${log.target_type} ${log.target_id}` : "-"}
                          </td>
                          <td className="px-4 py-3 text-muted-foreground">{log.details || "-"}</td>
                          <td className="px-4 py-3 text-muted-foreground whitespace-nowrap">{log.ip_address || "-"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          ) : tab === "feedback" ? (
            <div className="bg-white rounded-xl border border-border shadow-sm overflow-hidden">
              <div className="px-4 py-3 flex items-center justify-between border-b border-border bg-gray-50">
                <p className="text-sm text-muted-foreground">
                  {feedbackLoading ? "Loading…" : `${feedback.length} feedback submission(s)`}
                </p>
                <button
                  onClick={fetchFeedback}
                  className="px-3 py-1.5 rounded-lg bg-primary text-primary-foreground text-xs font-medium hover:bg-primary/90 transition-colors"
                >
                  Refresh
                </button>
              </div>
              {feedbackLoading ? (
                <div className="p-8 flex items-center justify-center gap-2 text-muted-foreground">
                  <Loader2 className="w-5 h-5 animate-spin" />
                  Loading feedback...
                </div>
              ) : feedbackError ? (
                <div className="p-4 text-red-700 bg-red-50">{feedbackError}</div>
              ) : feedback.length === 0 ? (
                <div className="p-8 text-center text-muted-foreground">
                  No feedback submitted yet. It'll show up here as soon as a user sends the
                  "send via form" feedback on the home page.
                </div>
              ) : (
                <div className="overflow-x-auto">
                  <table className="min-w-full text-sm">
                    <thead className="bg-gray-50 border-b border-border">
                      <tr>
                        <th className="px-4 py-3 text-left font-semibold text-foreground whitespace-nowrap">Name</th>
                        <th className="px-4 py-3 text-left font-semibold text-foreground whitespace-nowrap">Email</th>
                        <th className="px-4 py-3 text-left font-semibold text-foreground whitespace-nowrap">Rating</th>
                        <th className="px-4 py-3 text-left font-semibold text-foreground">Message</th>
                        <th className="px-4 py-3 text-left font-semibold text-foreground whitespace-nowrap">Submitted</th>
                      </tr>
                    </thead>
                    <tbody>
                      {feedback.map((f) => (
                        <tr key={f.id} className="border-b border-border align-top">
                          <td className="px-4 py-3 font-medium text-foreground whitespace-nowrap">{f.name || "-"}</td>
                          <td className="px-4 py-3 text-foreground whitespace-nowrap">{f.email || "-"}</td>
                          <td className="px-4 py-3 whitespace-nowrap">
                            {f.rating ? (
                              <span className="inline-flex items-center gap-0.5" title={`${f.rating}/5`}>
                                {[1, 2, 3, 4, 5].map((star) => (
                                  <Star
                                    key={star}
                                    className={`w-3.5 h-3.5 ${
                                      star <= f.rating!
                                        ? "text-amber-400 fill-current"
                                        : "text-slate-200 fill-current"
                                    }`}
                                  />
                                ))}
                              </span>
                            ) : (
                              <span className="text-xs text-muted-foreground">Not rated</span>
                            )}
                          </td>
                          <td className="px-4 py-3 text-foreground max-w-md whitespace-pre-wrap break-words">
                            {f.message || "-"}
                          </td>
                          <td className="px-4 py-3 text-muted-foreground whitespace-nowrap">
                            {formatIST(f.createdAt)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          ) : tab === "users" ? (
            <div className="bg-white rounded-xl border border-border shadow-sm overflow-hidden">
              <div className="px-4 py-3 flex items-center justify-between border-b border-border bg-gray-50">
                <p className="text-sm text-muted-foreground">
                  {usersLoading ? "Loading…" : `${users.length} registered user(s)`}
                </p>
                <button
                  onClick={fetchUsers}
                  className="px-3 py-1.5 rounded-lg bg-primary text-primary-foreground text-xs font-medium hover:bg-primary/90 transition-colors"
                >
                  Refresh
                </button>
              </div>
              {usersLoading ? (
                <div className="p-8 flex items-center justify-center gap-2 text-muted-foreground">
                  <Loader2 className="w-5 h-5 animate-spin" />
                  Loading registered users...
                </div>
              ) : usersError ? (
                <div className="p-4 text-red-700 bg-red-50">{usersError}</div>
              ) : users.length === 0 ? (
                <div className="p-8 text-center text-muted-foreground">No registered users yet.</div>
              ) : (
                <div className="overflow-x-auto">
                  <table className="min-w-full text-sm">
                    <thead className="bg-gray-50 border-b border-border">
                      <tr>
                        <th className="px-4 py-3 text-left font-semibold text-foreground whitespace-nowrap">Email</th>
                        <th className="px-4 py-3 text-left font-semibold text-foreground whitespace-nowrap">Name</th>
                        <th className="px-4 py-3 text-left font-semibold text-foreground whitespace-nowrap">Signed up via</th>
                        <th className="px-4 py-3 text-left font-semibold text-foreground whitespace-nowrap">Registered</th>
                        <th className="px-4 py-3 text-left font-semibold text-foreground whitespace-nowrap">Last Sign In</th>
                      </tr>
                    </thead>
                    <tbody>
                      {users.map((u) => (
                        <tr key={u.id} className="border-b border-border">
                          <td className="px-4 py-3 font-medium text-foreground">{u.email}</td>
                          <td className="px-4 py-3 text-foreground">{u.full_name || "-"}</td>
                          <td className="px-4 py-3 text-foreground capitalize">{u.provider || "email"}</td>
                          <td className="px-4 py-3 text-muted-foreground whitespace-nowrap">
                            {formatIST(u.created_at)}
                          </td>
                          <td className="px-4 py-3 text-muted-foreground whitespace-nowrap">
                            {u.last_sign_in_at ? formatIST(u.last_sign_in_at) : "Never"}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          ) : (
          <>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
            <div className="bg-white border border-border rounded-xl p-4 shadow-sm">
              <div className="flex items-center gap-2 text-muted-foreground text-sm mb-1"><Users className="w-4 h-4" /> Total Patients</div>
              <p className="text-2xl font-bold text-foreground">{stats.total}</p>
            </div>
            <button
              onClick={() => setRiskFilter(riskFilter === "High Risk" ? "" : "High Risk")}
              className={`bg-white border rounded-xl p-4 shadow-sm transition-all ${
                riskFilter === "High Risk"
                  ? "border-red-300 ring-2 ring-red-200 bg-red-50"
                  : "border-border hover:border-red-300 cursor-pointer"
              }`}
            >
              <div className="flex items-center gap-2 text-muted-foreground text-sm mb-1"><AlertTriangle className="w-4 h-4 text-red-500" /> High Risk</div>
              <p className="text-2xl font-bold text-red-600 text-left">{stats.highRisk}</p>
              {riskFilter === "High Risk" && <p className="text-xs text-red-600 mt-1 font-medium">Click to clear filter</p>}
            </button>
            <button
              onClick={() => setRiskFilter(riskFilter === "Medium Risk" ? "" : "Medium Risk")}
              className={`bg-white border rounded-xl p-4 shadow-sm transition-all ${
                riskFilter === "Medium Risk"
                  ? "border-amber-300 ring-2 ring-amber-200 bg-amber-50"
                  : "border-border hover:border-amber-300 cursor-pointer"
              }`}
            >
              <div className="flex items-center gap-2 text-muted-foreground text-sm mb-1"><ClipboardList className="w-4 h-4 text-amber-500" /> Medium Risk</div>
              <p className="text-2xl font-bold text-amber-600 text-left">{stats.moderateRisk}</p>
              {riskFilter === "Medium Risk" && <p className="text-xs text-amber-600 mt-1 font-medium">Click to clear filter</p>}
            </button>
            <div className="bg-white border border-border rounded-xl p-4 shadow-sm">
              <div className="flex items-center gap-2 text-muted-foreground text-sm mb-1"><Activity className="w-4 h-4 text-blue-500" /> Avg Confidence</div>
              <p className="text-2xl font-bold text-blue-600">{stats.avgConfidence}%</p>
            </div>
          </div>

          {loading ? (
            <div className="bg-white rounded-xl border border-border p-8 flex items-center justify-center gap-2 text-muted-foreground">
              <Loader2 className="w-5 h-5 animate-spin" />
              Loading patient records...
            </div>
          ) : error ? (
            <div className="bg-red-50 border border-red-200 rounded-xl p-4 text-red-700">{error}</div>
          ) : filteredRecords.length === 0 ? (
            <div className="bg-white rounded-xl border border-border p-8 text-center text-muted-foreground">
              {records.length === 0
                ? "No patient records yet. Submit an assessment first."
                : "No matching patients found for your search."}
            </div>
          ) : (
            <div className="bg-white rounded-xl border border-border shadow-sm overflow-hidden">
              <div className="px-4 py-3 text-sm text-muted-foreground border-b border-border bg-gray-50">
                Showing {filteredRecords.length} of {records.length} patient records
              </div>
              <div className="overflow-x-auto">
                <table className="min-w-full text-sm">
                  <thead className="bg-gray-50 border-b border-border">
                    <tr>
                      <th className="px-4 py-3 text-left font-semibold text-foreground whitespace-nowrap min-w-[150px]">Date</th>
                      <th className="px-4 py-3 text-left font-semibold text-foreground whitespace-nowrap min-w-[110px]">Patient</th>
                      <th className="px-4 py-3 text-left font-semibold text-foreground whitespace-nowrap min-w-[180px]">Email</th>
                      <th className="px-4 py-3 text-left font-semibold text-foreground whitespace-nowrap min-w-[60px]">Age</th>
                      <th className="px-4 py-3 text-left font-semibold text-foreground whitespace-nowrap min-w-[140px]">Disease</th>
                      <th className="px-4 py-3 text-left font-semibold text-foreground whitespace-nowrap min-w-[130px]">Risk</th>
                      <th className="px-4 py-3 text-left font-semibold text-foreground whitespace-nowrap min-w-[110px]">Confidence</th>
                      <th className="px-4 py-3 text-left font-semibold text-foreground whitespace-nowrap min-w-[120px]">Vitals</th>
                      <th className="px-4 py-3 text-left font-semibold text-foreground whitespace-nowrap min-w-[220px]">Symptoms</th>
                      <th className="px-4 py-3 text-left font-semibold text-foreground whitespace-nowrap min-w-[110px]">Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {filteredRecords.map((record) => (
                      <tr key={`${record.id}-${record.createdAt}`} className="border-b border-border align-top">
                        <td className="px-4 py-3 text-muted-foreground whitespace-nowrap">
                          {formatIST(record.createdAt)}
                        </td>
                        <td className="px-4 py-3 font-medium text-foreground">{record.patientName}</td>
                        <td className="px-4 py-3 text-muted-foreground">{record.email || "-"}</td>
                        <td className="px-4 py-3 text-foreground">{record.age || "-"}</td>
                        <td className="px-4 py-3 text-foreground">{record.predictedDisease}</td>
                        <td className="px-4 py-3 whitespace-nowrap">
                          <span
                            className={`inline-flex items-center justify-center px-2.5 py-1 rounded-full text-xs font-semibold whitespace-nowrap min-w-[84px] ${
                              record.riskLevel === "High Risk"
                                ? "bg-red-100 text-red-700"
                                : record.riskLevel === "Medium Risk"
                                  ? "bg-amber-100 text-amber-700"
                                  : "bg-emerald-100 text-emerald-700"
                            }`}
                          >
                            {record.riskLevel}
                          </span>
                          {/* Separate vitals-only score (BP/HR/temp/sugar/age) -- can
                              legitimately disagree with riskLevel above, which is
                              disease-severity x AI confidence. See PatientRecord comment. */}
                          <p className="text-xs text-muted-foreground mt-1 font-medium">
                            Vitals: {record.riskCategory} ({record.riskScore})
                          </p>
                        </td>
                        <td className="px-4 py-3 text-foreground">{record.confidence}%</td>
                        <td className="px-4 py-3 text-xs text-muted-foreground whitespace-nowrap">
                          BP {record.bloodPressureSystolic}/{record.bloodPressureDiastolic}<br />
                          HR {record.heartRate} bpm<br />
                          Temp {record.temperature} F
                        </td>
                        <td className="px-4 py-3 text-xs text-muted-foreground max-w-xs">
                          {(record.symptoms || []).join(", ") || "-"}
                        </td>
                        <td className="px-4 py-3 whitespace-nowrap">
                          <button
                            type="button"
                            onClick={() => setDeleteTarget(record.id)}
                            disabled={deletingId === record.id}
                            className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold rounded-md border border-red-200 text-red-600 hover:bg-red-50 disabled:opacity-60 transition-colors"
                          >
                            {deletingId === record.id ? (
                              <Loader2 className="w-3.5 h-3.5 animate-spin" />
                            ) : (
                              <Trash2 className="w-3.5 h-3.5" />
                            )}
                            Delete
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
          </>
          )}
        </div>

        <ConfirmDeleteModal
          open={deleteTarget != null}
          title="Delete this patient record?"
          message="This cannot be undone."
          loading={deletingId != null}
          onConfirm={() => deleteTarget != null && void handleDelete(deleteTarget)}
          onCancel={() => setDeleteTarget(null)}
        />

        <ConfirmDeleteModal
          open={confirmDeleteAll}
          title={`Delete ALL ${records.length} patient records?`}
          message="This action cannot be undone and is permanent."
          confirmLabel="Delete All"
          loading={deletingAll}
          onConfirm={() => void handleDeleteAll()}
          onCancel={() => setConfirmDeleteAll(false)}
        />
      </div>
    </Layout>
  );
}
