import { Layout } from "@/components/Layout";
import { ConfirmDeleteModal } from "@/components/ConfirmDeleteModal";
import { useEffect, useMemo, useRef, useState } from "react";
import { Activity, AlertTriangle, CheckCircle2, ClipboardList, Loader2, Lock, LogOut, Search, Stethoscope, Trash2, Users, UserPlus } from "lucide-react";

// Doctor accounts must use this domain (matches DOCTOR_EMAIL_DOMAIN_RE in
// app.py) -- not a real, verified mailbox, just the org's internal-account
// naming convention (mirrors is_valid_gmail_email's pattern for the login
// page, just a different required domain).
const DOCTOR_EMAIL_DOMAIN_RE = /^[A-Za-z0-9._%+-]+@ruralhealthcare\.com$/i;

// Same patient-records feature as AdminDashboard.tsx, behind its own
// separately-credentialed login gate (see DOCTOR_EMAIL/DOCTOR_PASSWORD in
// app.py's /doctor-login) -- a distinct role, not a reskin of the admin
// account itself. Reuses the same /patients REST endpoints since doctors
// and admins both view the same underlying patient assessment data.
const BACKEND = import.meta.env.VITE_BACKEND_URL || "http://127.0.0.1:5001";
const SESSION_KEY = "doctor_auth";

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
  // and confidence entirely. The two can legitimately disagree, which is
  // why the primary risk badge must show riskLevel, not riskCategory.
  riskLevel: string;
  riskCategory: string;
  riskScore: number;
  recommendation: string;
};

function DoctorLoginGate({ onAuth }: { onAuth: () => void }) {
  const [mode, setMode] = useState<"signin" | "register">("signin");
  const [email, setEmail]       = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError]       = useState("");
  const [registerSuccess, setRegisterSuccess] = useState("");
  const emailRef = useRef<HTMLInputElement>(null);

  useEffect(() => { emailRef.current?.focus(); }, [mode]);

  const switchMode = (next: "signin" | "register") => {
    setMode(next);
    setError("");
    setRegisterSuccess("");
    setPassword("");
    setConfirmPassword("");
  };

  const handleSignIn = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!email.trim() || !password.trim()) {
      setError("Both fields are required.");
      return;
    }
    setSubmitting(true);
    setError("");
    try {
      const resp = await fetch(`${BACKEND}/doctor-login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: email.trim(), password }),
      });
      const data = await resp.json();
      if (data.success) {
        sessionStorage.setItem(SESSION_KEY, "true");
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

  const handleRegister = async (e: React.FormEvent) => {
    e.preventDefault();
    const trimmedEmail = email.trim();
    if (!trimmedEmail || !password) {
      setError("Both fields are required.");
      return;
    }
    if (!DOCTOR_EMAIL_DOMAIN_RE.test(trimmedEmail)) {
      setError("Doctor email must end with @ruralhealthcare.com.");
      return;
    }
    if (password.length < 8) {
      setError("Password must be at least 8 characters.");
      return;
    }
    if (password !== confirmPassword) {
      setError("Passwords do not match.");
      return;
    }
    setSubmitting(true);
    setError("");
    try {
      const resp = await fetch(`${BACKEND}/doctor-register`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: trimmedEmail, password }),
      });
      const data = await resp.json();
      if (data.success) {
        switchMode("signin");
        setEmail(trimmedEmail);
        setRegisterSuccess("Account created! Please sign in below.");
      } else {
        setError(data.error || "Could not create account.");
      }
    } catch {
      setError("Cannot connect to backend. Make sure the server is running.");
    } finally {
      setSubmitting(false);
    }
  };

  const isRegister = mode === "register";

  return (
    <Layout>
      <div className="min-h-screen bg-gradient-to-br from-slate-50 via-sky-50/40 to-indigo-50/50 flex items-center justify-center px-4">
        <div className="w-full max-w-md">
          {/* Card */}
          <div className="bg-white border border-border rounded-2xl shadow-lg overflow-hidden">
            {/* Header */}
            <div className="bg-gradient-to-r from-sky-600 to-indigo-600 px-8 py-7 text-white text-center">
              <div className="flex justify-center mb-3">
                <div className="bg-white/20 rounded-full p-3">
                  <Stethoscope className="w-8 h-8" />
                </div>
              </div>
              <h1 className="text-2xl font-bold">{isRegister ? "Create Doctor Account" : "Doctor Access"}</h1>
              <p className="text-sm text-white/80 mt-1">
                {isRegister ? "Register with your @ruralhealthcare.com email" : "Sign in to view the patient dashboard"}
              </p>
            </div>

            {/* Form */}
            <form onSubmit={isRegister ? handleRegister : handleSignIn} className="px-8 py-7 space-y-5">
              {registerSuccess && !error && (
                <div className="flex items-start gap-2 bg-emerald-50 border border-emerald-200 text-emerald-700 text-sm rounded-lg px-4 py-3">
                  <CheckCircle2 className="w-4 h-4 shrink-0 mt-0.5" />
                  {registerSuccess}
                </div>
              )}
              {error && (
                <div className="bg-red-50 border border-red-200 text-red-700 text-sm rounded-lg px-4 py-3">
                  {error}
                </div>
              )}

              <div className="space-y-1.5">
                <label className="block text-sm font-medium text-foreground">Doctor Email</label>
                <input
                  ref={emailRef}
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="doctor@ruralhealthcare.com"
                  className="w-full border border-border rounded-lg px-4 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-sky-400/40 focus:border-sky-500 transition"
                  required
                />
                {isRegister && (
                  <p className="text-xs text-muted-foreground">Must end with @ruralhealthcare.com</p>
                )}
              </div>

              <div className="space-y-1.5">
                <label className="block text-sm font-medium text-foreground">Password</label>
                <input
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder={isRegister ? "At least 8 characters" : "Enter doctor password"}
                  className="w-full border border-border rounded-lg px-4 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-sky-400/40 focus:border-sky-500 transition"
                  required
                />
              </div>

              {isRegister && (
                <div className="space-y-1.5">
                  <label className="block text-sm font-medium text-foreground">Confirm Password</label>
                  <input
                    type="password"
                    value={confirmPassword}
                    onChange={(e) => setConfirmPassword(e.target.value)}
                    placeholder="Re-enter password"
                    className="w-full border border-border rounded-lg px-4 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-sky-400/40 focus:border-sky-500 transition"
                    required
                  />
                </div>
              )}

              <button
                type="submit"
                disabled={submitting}
                className="w-full flex items-center justify-center gap-2 bg-sky-600 text-white rounded-lg px-4 py-2.5 text-sm font-semibold hover:bg-sky-700 disabled:opacity-60 transition"
              >
                {submitting ? (
                  <Loader2 className="w-4 h-4 animate-spin" />
                ) : isRegister ? (
                  <UserPlus className="w-4 h-4" />
                ) : (
                  <Lock className="w-4 h-4" />
                )}
                {submitting ? "Please wait…" : isRegister ? "Create Account" : "Sign In"}
              </button>

              <button
                type="button"
                onClick={() => switchMode(isRegister ? "signin" : "register")}
                className="w-full text-center text-sm font-medium text-sky-600 hover:text-sky-700 transition-colors"
              >
                {isRegister ? "Already have an account? Sign in" : "New doctor? Create an account"}
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

export default function DoctorDashboard() {
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

  const logout = () => {
    sessionStorage.removeItem(SESSION_KEY);
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

  useEffect(() => {
    if (authenticated) fetchPatients();
  }, [authenticated]);

  // Confirmation now happens via the in-app ConfirmDeleteModal (see JSX
  // below) before either of these is ever called -- neither function
  // prompts on its own anymore.
  const handleDelete = async (id: number) => {
    setDeletingId(id);
    setError("");
    try {
      const response = await fetch(`${BACKEND}/patients/${id}`, { method: "DELETE" });
      const data = await response.json();
      if (!response.ok || !data.success) {
        setError(data.error || "Failed to delete patient record.");
        return;
      }
      setRecords((prev) => prev.filter((record) => record.id !== id));
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
      const response = await fetch(`${BACKEND}/patients/delete/all`, { method: "DELETE" });
      const data = await response.json();
      if (!response.ok || !data.success) {
        setError(data.error || "Failed to delete all patient records.");
        return;
      }
      setRecords([]);
      setRiskFilter("");
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
    return <DoctorLoginGate onAuth={() => setAuthenticated(true)} />;
  }

  return (
    <Layout>
      <div className="min-h-screen bg-gradient-to-br from-slate-50 via-sky-50/40 to-indigo-50/50 py-8">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 mb-6">
            <div>
              <h1 className="text-3xl font-bold text-foreground">Doctor Dashboard</h1>
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
                  className="w-full sm:w-80 border border-border rounded-lg pl-9 pr-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-sky-400/40 focus:border-sky-500 transition"
                />
              </div>
              <button
                onClick={fetchPatients}
                className="px-4 py-2 rounded-lg bg-sky-600 text-white text-sm font-medium hover:bg-sky-700 transition-colors"
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
                          {new Date(record.createdAt).toLocaleString()}
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
