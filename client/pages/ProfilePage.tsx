import { Layout } from "@/components/Layout";
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Activity,
  AlertOctagon,
  AlertTriangle,
  Calendar,
  Check,
  ChevronDown,
  ChevronUp,
  ClipboardList,
  Droplets,
  Heart,
  Info,
  LogOut,
  Stethoscope,
  Thermometer,
  Trash2,
  TrendingDown,
  User,
} from "lucide-react";
import { backfillRiskLevels, riskCaption } from "@/utils/risk";

interface AssessmentRecord {
  id: number;
  date: string;
  patientName: string;
  age: string;
  symptoms: string[];
  vitals: {
    bp: string;
    heartRate: string;
    temperature: string;
    sugar: string;
  };
  predicted_disease: string;
  confidence: number;
  risk_category: string;
  risk_score: number;
  risk_level?: string;
  emergency_alert?: boolean;
  recommendation: string;
}

interface StoredUser {
  fullName?: string;
  name?: string;
  email: string;
  googleAuth?: boolean;
}

function RiskBadge({ risk }: { risk: string }) {
  const level = risk || "";
  const isLow = level === "Low Risk";
  const isMedium = level === "Medium Risk";
  const isHigh = level === "High Risk";
  const styles = isHigh
    ? "bg-red-100 text-red-700"
    : isMedium
    ? "bg-amber-100 text-amber-700"
    : "bg-green-100 text-green-700";
  return (
    <span className={`inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-semibold ${styles}`}>
      {isHigh ? (
        <AlertOctagon className="w-3 h-3" />
      ) : isMedium ? (
        <AlertTriangle className="w-3 h-3" />
      ) : (
        <Check className="w-3 h-3" />
      )}
      {level || "Unknown"}
    </span>
  );
}

function AssessmentCard({ record, onDelete }: { record: AssessmentRecord; onDelete: (id: number) => void }) {
  const [expanded, setExpanded] = useState(false);
  const dateStr = new Date(record.date).toLocaleDateString("en-US", {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });

  return (
    <div className="bg-white border border-border rounded-2xl shadow-sm overflow-hidden">
      {/* Card Header */}
      <div className="flex items-center justify-between p-4 sm:p-5">
        <div className="flex items-center gap-3 min-w-0">
          <div className="w-10 h-10 bg-primary/10 rounded-xl flex items-center justify-center flex-shrink-0">
            <Stethoscope className="w-5 h-5 text-primary" />
          </div>
          <div className="min-w-0">
            <p className="font-semibold text-foreground text-sm truncate">{record.predicted_disease}</p>
            <div className="flex items-center gap-2 flex-wrap mt-0.5">
              <span className="text-xs text-muted-foreground flex items-center gap-1">
                <User className="w-3 h-3" /> {record.patientName}, {record.age} yrs
              </span>
              <span className="text-xs text-muted-foreground flex items-center gap-1">
                <Calendar className="w-3 h-3" /> {dateStr}
              </span>
            </div>
          </div>
        </div>
        <div className="flex items-center gap-2 flex-shrink-0 ml-2">
          <div className="flex flex-col items-end gap-1">
            <RiskBadge risk={record.risk_level ?? record.risk_category} />
            <p className="max-w-[260px] text-right text-[10px] leading-snug text-muted-foreground">
              {riskCaption(record.predicted_disease, record.confidence, record.emergency_alert)}
            </p>
          </div>
          <button
            onClick={() => setExpanded((v) => !v)}
            className="p-1.5 rounded-lg hover:bg-primary/10 text-muted-foreground hover:text-primary transition-colors"
            aria-label={expanded ? "Collapse" : "Expand"}
          >
            {expanded ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
          </button>
          <button
            onClick={() => onDelete(record.id)}
            className="p-1.5 rounded-lg hover:bg-red-50 text-muted-foreground hover:text-red-600 transition-colors"
            aria-label="Delete record"
          >
            <Trash2 className="w-4 h-4" />
          </button>
        </div>
      </div>

      {/* Confidence bar */}
      <div className="px-5 pb-4">
        <div className="flex items-center justify-between mb-1">
          <span className="text-xs text-muted-foreground flex items-center gap-1">
            Prediction Certainty
            <span className="relative inline-flex items-center group" role="note">
              <Info className="w-3 h-3 text-muted-foreground cursor-help" />
              <span className="pointer-events-none absolute bottom-full left-1/2 z-20 mb-1.5 hidden w-52 -translate-x-1/2 rounded-md bg-gray-800 px-2 py-1.5 text-[11px] font-normal leading-snug text-white group-hover:block">
                This shows how sure the AI model is about this specific prediction — not how
                dangerous the condition is. See the note under Risk Level for that.
              </span>
            </span>
          </span>
          <span className="text-xs font-bold text-primary">{record.confidence}%</span>
        </div>
        <div className="w-full bg-gray-100 rounded-full h-1.5">
          <div
            className={`h-1.5 rounded-full ${
              record.confidence >= 70
                ? "bg-green-500"
                : record.confidence >= 40
                ? "bg-yellow-500"
                : "bg-red-500"
            }`}
            style={{ width: `${record.confidence}%` }}
          />
        </div>
      </div>

      {/* Expanded Details */}
      {expanded && (
        <div className="border-t border-border px-5 py-4 space-y-4 bg-gray-50/50">
          {/* Vitals */}
          <div>
            <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wide mb-2">Vital Signs</p>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
              <div className="bg-white border border-border rounded-xl p-3 text-center">
                <Heart className="w-4 h-4 text-rose-500 mx-auto mb-1" />
                <p className="text-xs text-muted-foreground">Blood Pressure</p>
                <p className="text-sm font-bold text-foreground">{record.vitals.bp} mmHg</p>
              </div>
              <div className="bg-white border border-border rounded-xl p-3 text-center">
                <Activity className="w-4 h-4 text-blue-500 mx-auto mb-1" />
                <p className="text-xs text-muted-foreground">Heart Rate</p>
                <p className="text-sm font-bold text-foreground">{record.vitals.heartRate} bpm</p>
              </div>
              <div className="bg-white border border-border rounded-xl p-3 text-center">
                <Thermometer className="w-4 h-4 text-orange-500 mx-auto mb-1" />
                <p className="text-xs text-muted-foreground">Temperature</p>
                <p className="text-sm font-bold text-foreground">{record.vitals.temperature} °F</p>
              </div>
              <div className="bg-white border border-border rounded-xl p-3 text-center">
                <Droplets className="w-4 h-4 text-purple-500 mx-auto mb-1" />
                <p className="text-xs text-muted-foreground">Sugar Level</p>
                <p className="text-sm font-bold text-foreground">{record.vitals.sugar} mg/dL</p>
              </div>
            </div>
          </div>

          {/* Symptoms */}
          {record.symptoms.length > 0 && (
            <div>
              <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wide mb-2">Reported Symptoms</p>
              <div className="flex flex-wrap gap-1.5">
                {record.symptoms.map((s) => (
                  <span key={s} className="px-2.5 py-1 bg-primary/10 text-primary text-xs font-medium rounded-full">
                    {s}
                  </span>
                ))}
              </div>
            </div>
          )}

          {/* Recommendation */}
          <div>
            <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wide mb-1">Recommendation</p>
            <p className="text-sm text-foreground leading-relaxed">{record.recommendation}</p>
          </div>

          {/* Risk-level explainer */}
          <div>
            <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wide mb-1">About the Risk Level</p>
            <p className="text-sm text-muted-foreground leading-relaxed">
              Risk level considers both how certain the model is AND how medically serious this
              condition is. A highly certain prediction of a less dangerous condition is still
              lower risk than an uncertain prediction of a dangerous one.
            </p>
          </div>
        </div>
      )}
    </div>
  );
}

export default function ProfilePage() {
  const navigate = useNavigate();
  const [user, setUser] = useState<StoredUser | null>(null);
  const [assessments, setAssessments] = useState<AssessmentRecord[]>([]);
  const assessmentRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const stored = localStorage.getItem("user");
    if (!stored) {
      navigate("/login");
      return;
    }
    const parsedUser: StoredUser = JSON.parse(stored);
    setUser(parsedUser);

    const key = `assessments_${parsedUser.email}`;
    const records = JSON.parse(localStorage.getItem(key) || "[]");
    const backfilled = backfillRiskLevels(records);
    if (backfilled > 0) {
      localStorage.setItem(key, JSON.stringify(records));
      console.info(
        `[risk-migration] backfilled risk_level for ${backfilled} legacy assessment record(s) for ${parsedUser.email}`
      );
    }
    setAssessments(records);
  }, [navigate]);

  // Scroll to assessments section if URL has #assessments
  useEffect(() => {
    if (window.location.hash === "#assessments" && assessmentRef.current) {
      assessmentRef.current.scrollIntoView({ behavior: "smooth" });
    }
  }, [assessments]);

  const handleDelete = (id: number) => {
    if (!user) return;
    const key = `assessments_${user.email}`;
    const updated = assessments.filter((a) => a.id !== id);
    setAssessments(updated);
    localStorage.setItem(key, JSON.stringify(updated));
  };

  const handleLogout = () => {
    localStorage.removeItem("user");
    navigate("/");
  };

  const displayName = user?.fullName || user?.name || user?.email || "";
  const initials = displayName
    .split(" ")
    .map((n) => n[0])
    .join("")
    .toUpperCase()
    .slice(0, 2);

  if (!user) return null;

  return (
    <Layout>
      <div className="bg-gradient-to-br from-primary/5 via-accent/5 to-secondary/5 min-h-screen py-8 md:py-12">
        <div className="max-w-4xl mx-auto px-4 sm:px-6 lg:px-8 space-y-8">

          {/* Profile Card */}
          <div className="bg-white rounded-2xl shadow-lg border border-border overflow-hidden">
            <div className="bg-gradient-to-r from-primary to-accent relative" style={{ height: "30px" }} />
            <div className="px-6 pb-6 pt-2">
              <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 -mt-6">
                <div className="flex items-center gap-4">
                  <div className="w-16 h-16 rounded-full bg-primary border-4 border-white shadow-lg flex items-center justify-center text-white text-xl font-bold flex-shrink-0">
                    {initials || <User className="w-6 h-6" />}
                  </div>
                  <div className="mt-4">
                    <h1 className="text-xl font-bold text-foreground">{displayName}</h1>
                    <p className="text-sm text-muted-foreground">{user.email}</p>
                    {user.googleAuth && (
                      <span className="inline-flex items-center gap-1 px-2 py-0.5 bg-blue-100 text-blue-700 text-xs font-medium rounded-full mt-1">
                        Google Account
                      </span>
                    )}
                  </div>
                </div>
                <button
                  onClick={handleLogout}
                  className="flex items-center gap-2 px-4 py-2 text-sm text-red-600 border border-red-200 rounded-lg hover:bg-red-50 transition-colors self-start sm:self-auto"
                >
                  <LogOut className="w-4 h-4" />
                  Logout
                </button>
              </div>
            </div>
          </div>

          {/* Stats Row */}
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
            <div className="bg-white border border-border rounded-2xl p-5 shadow-sm text-center">
              <p className="text-3xl font-bold text-primary">{assessments.length}</p>
              <p className="text-sm text-muted-foreground mt-1">Total Assessments</p>
            </div>
            <div className="bg-white border border-border rounded-2xl p-5 shadow-sm text-center">
              <p className="text-3xl font-bold text-green-600">
                {assessments.filter((a) => (a.risk_level ?? a.risk_category) === "Low Risk").length}
              </p>
              <p className="text-sm text-muted-foreground mt-1">Low Risk</p>
            </div>
            <div className="bg-white border border-border rounded-2xl p-5 shadow-sm text-center">
              <p className="text-3xl font-bold text-amber-600">
                {assessments.filter((a) => (a.risk_level ?? a.risk_category) === "Medium Risk").length}
              </p>
              <p className="text-sm text-muted-foreground mt-1">Medium Risk</p>
            </div>
            <div className="bg-white border border-border rounded-2xl p-5 shadow-sm text-center col-span-2 sm:col-span-1">
              <p className="text-3xl font-bold text-red-600">
                {assessments.filter((a) => (a.risk_level ?? a.risk_category) === "High Risk").length}
              </p>
              <p className="text-sm text-muted-foreground mt-1">High Risk</p>
            </div>
          </div>

          {/* Assessments Section */}
          <div ref={assessmentRef} id="assessments">
            <div className="flex items-center gap-3 mb-4">
              <div className="w-9 h-9 bg-primary/10 rounded-xl flex items-center justify-center">
                <ClipboardList className="w-5 h-5 text-primary" />
              </div>
              <h2 className="text-xl font-bold text-foreground">My Assessment History</h2>
            </div>

            {assessments.length === 0 ? (
              <div className="bg-white border border-border rounded-2xl p-12 text-center shadow-sm">
                <div className="w-16 h-16 bg-primary/10 rounded-full flex items-center justify-center mx-auto mb-4">
                  <ClipboardList className="w-8 h-8 text-primary" />
                </div>
                <h3 className="text-lg font-semibold text-foreground mb-2">No assessments yet</h3>
                <p className="text-muted-foreground text-sm mb-6">
                  Run a health assessment and your results will appear here.
                </p>
                <a
                  href="/assess"
                  className="inline-flex items-center gap-2 px-5 py-2.5 bg-primary text-primary-foreground font-semibold rounded-lg hover:bg-primary/90 transition-colors text-sm"
                >
                  Start Assessment
                </a>
              </div>
            ) : (
              <div className="space-y-4">
                {assessments.map((record) => (
                  <AssessmentCard key={record.id} record={record} onDelete={handleDelete} />
                ))}
              </div>
            )}
          </div>

        </div>
      </div>
    </Layout>
  );
}
