import { useEffect, useState } from "react";
import {
  Activity,
  AlertOctagon,
  AlertTriangle,
  Brain,
  Check,
  CheckCircle2,
  ChevronDown,
  ClipboardList,
  Droplets,
  Heart,
  Info,
  Plus,
  Printer,
  ShieldAlert,
  Stethoscope,
  Thermometer,
  TrendingDown,
  User,
} from "lucide-react";
import { riskCaption } from "@/utils/risk";

export interface ReportPrediction {
  disease: string;
  confidence: number;
  precautions: string[];
}

export interface ReportModelAgreement {
  rf: string;
  nb: string;
  svm: string;
  all_agree: boolean;
}

export interface AssessmentReportData {
  predicted_disease: string;
  confidence: number;
  recommendation: string;
  guarded_recommendation?: string;
  predictions: ReportPrediction[];
  confidence_band: "HIGH" | "MEDIUM" | "LOW";
  top1_vs_top2_margin: number;
  model_agreement?: ReportModelAgreement;
  flags: string[];
  confusable_with_note: string | null;
  emergency_alert: boolean;
  risk_level?: string;
  matched_symptoms: string[];
  ignored_checkboxes: string[];
  disclaimer: string;
}

export interface ReportPatient {
  patientName: string;
  age: string;
  gender: string;
  bloodPressureSystolic: string;
  bloodPressureDiastolic: string;
  heartRate: string;
  temperature: string;
  sugarLevel: string;
}

interface AssessmentReportProps {
  result: AssessmentReportData;
  patient: ReportPatient;
  onStartNew: () => void;
}

type Band = "HIGH" | "MEDIUM" | "LOW";

const pct = (value: number | undefined): string => {
  const v = Number(value ?? 0);
  if (Number.isNaN(v)) return "0.0";
  const percent = v > 1 ? v : v * 100;
  return percent.toFixed(1);
};

const BAND_META: Record<
  Band,
  {
    label: string;
    pillClass: string;
    barClass: string;
    explanation: string;
  }
> = {
  HIGH: {
    label: "High Confidence",
    pillClass: "bg-green-100 text-green-700",
    barClass: "bg-gradient-to-r from-green-500 to-green-600",
    explanation:
      "The model is confident in this result. Still confirm with a healthcare provider before treatment.",
  },
  MEDIUM: {
    label: "Medium Confidence",
    pillClass: "bg-amber-100 text-amber-700",
    barClass: "bg-gradient-to-r from-amber-400 to-amber-500",
    explanation:
      "The model has moderate confidence. Consider this a likely possibility — see a doctor to confirm.",
  },
  LOW: {
    label: "Low Confidence",
    pillClass: "bg-orange-100 text-orange-700",
    barClass: "bg-gradient-to-r from-orange-400 to-orange-500",
    explanation:
      "The model is uncertain. Treat this as a starting point only — multiple conditions show similar symptoms. Please consult a doctor.",
  },
};

const deriveBand = (confidence: number): Band => {
  const c = Number(confidence ?? 0);
  const percent = c > 1 ? c : c * 100;
  if (percent >= 70) return "HIGH";
  if (percent >= 50) return "MEDIUM";
  return "LOW";
};

const prettyToken = (token: string): string =>
  token
    .replace(/_/g, " ")
    .split(" ")
    .filter(Boolean)
    .map(word => word.charAt(0).toUpperCase() + word.slice(1))
    .join(" ");

interface VitalStatus {
  label: string;
  text: string;
  dot: string;
  badge: string;
}

const normal: VitalStatus = { label: "Normal", text: "text-green-700", dot: "bg-green-500", badge: "bg-green-100 text-green-700" };
const borderline: VitalStatus = { label: "Borderline", text: "text-orange-700", dot: "bg-orange-500", badge: "bg-orange-100 text-orange-700" };
const high: VitalStatus = { label: "High", text: "text-red-700", dot: "bg-red-500", badge: "bg-red-100 text-red-700" };
const critical: VitalStatus = { label: "Critical", text: "text-red-700", dot: "bg-red-500", badge: "bg-red-100 text-red-700" };
const low: VitalStatus = { label: "Low", text: "text-red-700", dot: "bg-red-500", badge: "bg-red-100 text-red-700" };

const bpStatus = (sys: number, dia: number): VitalStatus => {
  if (sys >= 140 || dia >= 90) return high;
  if (sys >= 120 || dia >= 80) return borderline;
  return normal;
};

// Standard clinical bands: Normal 60-100, Elevated/Tachycardia 101-120,
// High 121-150, Critical above 150 or below 40. 40-59 (mild bradycardia)
// isn't one of those 4 named bands but must not silently fall through to
// "Normal" either, so it's shown as the existing "Low" status.
const hrStatus = (hr: number): VitalStatus => {
  if (hr > 150 || hr < 40) return critical;
  if (hr > 120) return high;
  if (hr > 100) return { ...borderline, label: "Elevated" };
  if (hr >= 60) return normal;
  return low;
};

const tempStatus = (temp: number): VitalStatus => {
  if (temp > 100.4) return { ...high, label: "High Fever" };
  if (temp >= 99) return { ...borderline, label: "Low Grade Fever" };
  return normal;
};

const sugarStatus = (sugar: number): VitalStatus => {
  if (sugar > 125) return { ...high, label: "Diabetic Range" };
  if (sugar >= 100) return { ...borderline, label: "Prediabetic Range" };
  if (sugar < 70) return { ...low, label: "Below Normal" };
  return normal;
};

const FLAG_TRANSLATIONS: Record<
  string,
  { icon: "warn" | "info" | "danger"; title: string; text: string }
> = {
  LOW_CONFIDENCE: {
    icon: "warn",
    title: "Low Confidence Prediction",
    text: "Low confidence prediction — symptoms may match multiple conditions",
  },
  MEDIUM_CONFIDENCE: {
    icon: "info",
    title: "Moderate Confidence",
    text: "Moderate confidence — consider this alongside other possibilities",
  },
  AMBIGUOUS_TOP_CANDIDATES: {
    icon: "warn",
    title: "Ambiguous Top Candidates",
    text: "Two conditions scored very similarly — hard to distinguish from symptoms alone",
  },
  MODEL_DISAGREEMENT: {
    icon: "warn",
    title: "Model Disagreement",
    text: "The three prediction models gave different results — treat with caution",
  },
  TOO_FEW_SYMPTOMS: {
    icon: "danger",
    title: "Too Few Symptoms",
    text: "Too few symptoms selected for a reliable prediction — please add more symptoms and reassess",
  },
};

function ConfidenceBar({
  percent,
  band,
  small,
}: {
  percent: number;
  band: Band;
  small?: boolean;
}) {
  const [width, setWidth] = useState(0);

  useEffect(() => {
    const timer = setTimeout(() => setWidth(percent), 120);
    return () => clearTimeout(timer);
  }, [percent]);

  return (
    <div
      className={`w-full ${small ? "h-2" : "h-3.5"} bg-gray-100 rounded-full overflow-hidden border border-gray-200`}
    >
      <div
        className={`h-full ${BAND_META[band].barClass} rounded-full`}
        style={{ width: `${width}%`, transition: "width 0.8s ease-out" }}
      />
    </div>
  );
}

function SectionHeader({
  icon,
  title,
  right,
}: {
  icon: React.ReactNode;
  title: string;
  right?: React.ReactNode;
}) {
  return (
    <div className="flex items-center gap-3 mb-5">
      <div className="w-10 h-10 bg-primary/10 rounded-xl flex items-center justify-center flex-shrink-0">
        {icon}
      </div>
      <h3 className="text-lg font-bold text-foreground">{title}</h3>
      {right && <div className="ml-auto">{right}</div>}
    </div>
  );
}

function RiskBadge({ risk }: { risk: string }) {
  const level = risk || "";
  const isLow = level === "Low Risk";
  const isMedium = level === "Medium Risk";
  const isHigh = level === "High Risk";
  const styles = isHigh
    ? "bg-red-100 text-red-700 border-red-200"
    : isMedium
    ? "bg-amber-100 text-amber-700 border-amber-200"
    : "bg-green-100 text-green-700 border-green-200";
  return (
    <span
      className={`inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-bold uppercase border ${styles}`}
    >
      {isHigh ? (
        <AlertOctagon className="w-3.5 h-3.5" />
      ) : isMedium ? (
        <AlertTriangle className="w-3.5 h-3.5" />
      ) : (
        <TrendingDown className="w-3.5 h-3.5" />
      )}
      {level || "Unknown"}
    </span>
  );
}

function VitalMetric({
  icon,
  label,
  value,
  unit,
  status,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
  unit: string;
  status: VitalStatus;
}) {
  return (
    <div className="rounded-xl border border-border bg-gray-50 p-4">
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2">
          {icon}
          <span className="text-sm font-semibold text-foreground">{label}</span>
        </div>
        <span
          className={`flex items-center gap-1.5 text-xs font-bold px-2.5 py-1 rounded-full ${status.badge}`}
        >
          <span className={`w-2 h-2 rounded-full inline-block ${status.dot}`} />
          {status.label}
        </span>
      </div>
      <p className={`text-2xl font-bold ${status.text}`}>
        {value}
        <span className="text-sm font-semibold text-muted-foreground ml-1.5">{unit}</span>
      </p>
    </div>
  );
}

export default function AssessmentReport({
  result,
  patient,
  onStartNew,
}: AssessmentReportProps) {
  const [showAlternatives, setShowAlternatives] = useState(false);

  const predictions = result.predictions ?? [];
  const top = predictions[0];
  const band: Band = result.confidence_band || deriveBand(top?.confidence);
  const bandMeta = BAND_META[band];
  const topPercent = parseFloat(pct(top?.confidence));
  const emergency = !!result.emergency_alert;
  const agreement = result.model_agreement ?? { rf: "", nb: "", svm: "", all_agree: false };
  const modelsAllAgree = !!agreement.all_agree;
  const flags = (result.flags ?? []).filter(f => FLAG_TRANSLATIONS[f]);
  const ignored = result.ignored_checkboxes ?? [];
  const matched = result.matched_symptoms ?? [];

  const bannerText = emergency
    ? (result.guarded_recommendation || result.recommendation || "").replace(/^URGENT:\s*/i, "")
    : "";

  const sys = parseFloat(patient.bloodPressureSystolic);
  const dia = parseFloat(patient.bloodPressureDiastolic);
  const hr = parseFloat(patient.heartRate);
  const temp = parseFloat(patient.temperature);
  const sugar = parseFloat(patient.sugarLevel);
  const hasBP = Number.isFinite(sys) && Number.isFinite(dia);
  const hasHR = Number.isFinite(hr);
  const hasTemp = Number.isFinite(temp);
  const hasSugar = Number.isFinite(sugar);

  const modelPill = (code: string, fullName: string, disease: string) => (
    <div
      key={code}
      className={`rounded-xl border p-3 text-center ${
        modelsAllAgree ? "bg-green-50 border-green-200" : "bg-orange-50 border-orange-200"
      }`}
    >
      <div className="flex items-center justify-center gap-1.5">
        {modelsAllAgree && <Check className="w-3.5 h-3.5 text-green-600 flex-shrink-0" />}
        <span
          className={`text-[11px] font-extrabold uppercase tracking-wide ${
            modelsAllAgree ? "text-green-700" : "text-orange-700"
          }`}
        >
          {code}
        </span>
      </div>
      <p
        className={`text-[11px] font-medium mt-0.5 ${
          modelsAllAgree ? "text-green-700/80" : "text-orange-700/80"
        }`}
      >
        {fullName}
      </p>
      <p className="text-sm font-bold text-foreground mt-1 break-words">{disease}</p>
    </div>
  );

  return (
    <div id="assessment-report" className="space-y-5">
      <style>{`
        @keyframes emergency-pulse {
          0%, 100% { box-shadow: 0 0 0 0 rgba(220, 38, 38, 0.55); }
          50% { box-shadow: 0 0 0 10px rgba(220, 38, 38, 0); }
        }
        .emergency-banner {
          animation: emergency-pulse 1.6s ease-out infinite;
        }
        @media print {
          header, footer, .report-actions, .report-side-gutter { display: none !important; }
          main { padding: 0 !important; }
          body { background: white !important; }
          * { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
          .report-card { box-shadow: none !important; border-color: #d1d5db !important; }
          .emergency-banner { animation: none !important; box-shadow: none !important; }
          .transition-all { transition: none !important; }
        }
      `}</style>

      {emergency && (
        <div className="emergency-banner rounded-2xl bg-red-600 border-2 border-red-700 p-5 md:p-6 shadow-xl">
          <div className="flex items-start gap-4">
            <div className="w-11 h-11 bg-white/15 rounded-xl flex items-center justify-center flex-shrink-0">
              <AlertTriangle className="w-7 h-7 text-white" />
            </div>
            <div className="flex-1 min-w-0">
              <p className="text-xl md:text-2xl font-extrabold text-white leading-snug">
                URGENT — {bannerText || "immediate clinical evaluation required"}
              </p>
              <p className="text-sm md:text-base text-red-100 mt-2 font-medium">
                Please seek immediate in-person clinical evaluation. Do not rely on this
                screening result alone.
              </p>
            </div>
          </div>
        </div>
      )}

      {/* Section 2 — Patient Summary Card */}
      <div className="bg-white rounded-2xl shadow-lg p-6 md:p-8 border border-border print:shadow-none print:rounded-none report-card">
        <SectionHeader
          icon={<ClipboardList className="w-5 h-5 text-primary" />}
          title="Assessment Summary"
          right={
            <div className="text-right">
              <p className="text-sm font-bold text-foreground">{patient.patientName || "Patient"}</p>
              <p className="text-xs text-muted-foreground">
                {new Date().toLocaleDateString("en-US", {
                  year: "numeric",
                  month: "long",
                  day: "numeric",
                })}
              </p>
            </div>
          }
        />

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 mb-4">
          <div className="rounded-xl border border-border bg-gray-50 p-4">
            <div className="flex items-center gap-2 mb-2">
              <User className="w-4 h-4 text-primary" />
              <span className="text-sm font-semibold text-foreground">Patient Demographics</span>
            </div>
            <p className="text-lg font-bold text-foreground">
              {patient.age ? `${patient.age} years` : "N/A"}
              <span className="text-sm font-semibold text-muted-foreground mx-2">•</span>
              {patient.gender || "N/A"}
            </p>
          </div>
          <div className="rounded-xl border border-border bg-gray-50 p-4">
            <div className="flex items-center gap-2 mb-2">
              <Stethoscope className="w-4 h-4 text-primary" />
              <span className="text-sm font-semibold text-foreground">Symptoms Assessed</span>
              <span className="ml-auto text-xs font-bold bg-teal-100 text-teal-700 px-2 py-0.5 rounded-full">
                {matched.length} matched
              </span>
            </div>
            <p className="text-sm text-muted-foreground">
              {matched.length > 0
                ? `${matched.length} symptom(s) recognised by the model`
                : "No symptoms recognised"}
            </p>
          </div>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <VitalMetric
            icon={<Heart className="w-4 h-4 text-rose-500" />}
            label="Blood Pressure"
            value={hasBP ? `${patient.bloodPressureSystolic}/${patient.bloodPressureDiastolic}` : "N/A"}
            unit="mmHg"
            status={hasBP ? bpStatus(sys, dia) : normal}
          />
          <VitalMetric
            icon={<Activity className="w-4 h-4 text-pink-500" />}
            label="Heart Rate"
            value={hasHR ? patient.heartRate : "N/A"}
            unit="bpm"
            status={hasHR ? hrStatus(hr) : normal}
          />
          <VitalMetric
            icon={<Thermometer className="w-4 h-4 text-orange-500" />}
            label="Temperature"
            value={hasTemp ? patient.temperature : "N/A"}
            unit="°F"
            status={hasTemp ? tempStatus(temp) : normal}
          />
          <VitalMetric
            icon={<Droplets className="w-4 h-4 text-blue-500" />}
            label="Blood Sugar"
            value={hasSugar ? patient.sugarLevel : "N/A"}
            unit="mg/dL"
            status={hasSugar ? sugarStatus(sugar) : normal}
          />
        </div>

        {matched.length > 0 && (
          <div className="mt-5">
            <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wide mb-2">
              Symptoms assessed
            </p>
            <div className="flex flex-wrap gap-2">
              {matched.map(symptom => (
                <span
                  key={symptom}
                  className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-teal-50 text-teal-700 font-semibold text-sm rounded-full border border-teal-200"
                >
                  <span className="w-1.5 h-1.5 bg-teal-500 rounded-full" />
                  {prettyToken(symptom)}
                </span>
              ))}
            </div>
          </div>
        )}

        {ignored.length > 0 && (
          <p className="mt-4 text-xs text-muted-foreground bg-gray-50 border border-border rounded-lg px-3 py-2">
            Note: {ignored.length} symptom(s) not recognised by this model:{" "}
            <span className="font-semibold text-foreground">{ignored.join(", ")}</span>
          </p>
        )}
      </div>

      {/* Section 3 — Top Prediction */}
      {top && (
        <div className="bg-white rounded-2xl shadow-lg p-6 md:p-8 border border-border print:shadow-none print:rounded-none report-card">
          <SectionHeader
            icon={<Stethoscope className="w-5 h-5 text-primary" />}
            title="Primary Prediction"
          />

          <div className="bg-gradient-to-br from-primary/10 via-accent/5 to-transparent rounded-2xl p-6 border border-primary/20 print:bg-white print:border-gray-200">
            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 mb-6">
              <div>
                <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wide mb-1">
                  Most likely condition
                </p>
                <h4 className="text-3xl md:text-4xl font-extrabold text-primary">
                  {top.disease}
                </h4>
                <div className="mt-2">
                  <RiskBadge risk={result.risk_level ?? ""} />
                  <p className="mt-1 max-w-[320px] text-[11px] leading-snug text-muted-foreground">
                    {riskCaption(top.disease, top.confidence, emergency)}
                  </p>
                </div>
              </div>
              <div className="text-left sm:text-right">
                <p className="text-3xl md:text-4xl font-extrabold text-foreground">
                  {pct(top.confidence)}%
                </p>
                <p className="text-sm font-semibold text-muted-foreground flex items-center sm:justify-end gap-1">
                  Prediction Certainty
                  <span className="relative inline-flex items-center group" role="note">
                    <Info className="w-3 h-3 text-muted-foreground cursor-help" />
                    <span className="pointer-events-none absolute bottom-full right-0 z-20 mb-1.5 hidden w-52 rounded-md bg-gray-800 px-2 py-1.5 text-[11px] font-normal leading-snug text-white group-hover:block">
                      This shows how sure the AI model is about this specific prediction — not how
                      dangerous the condition is. See the note under Risk Level for that.
                    </span>
                  </span>
                </p>
              </div>
            </div>

            <div className="space-y-2">
              <div className="flex items-center justify-between gap-3">
                <span
                  className={`inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-bold uppercase ${bandMeta.pillClass}`}
                >
                  {bandMeta.label}
                </span>
                <span className="text-xl font-bold text-foreground">{pct(top.confidence)}%</span>
              </div>
              <ConfidenceBar percent={topPercent} band={band} />
              <p className="text-sm text-muted-foreground leading-relaxed pt-1">
                {bandMeta.explanation}
              </p>
            </div>

            <div className="mt-6">
              <div className="flex items-center gap-2 mb-3">
                <Brain className="w-4 h-4 text-primary" />
                <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wide">
                  Model Agreement
                </p>
                <span
                  className={`ml-auto inline-flex items-center gap-1 text-xs font-bold px-2.5 py-1 rounded-full ${
                    modelsAllAgree ? "bg-green-100 text-green-700" : "bg-orange-100 text-orange-700"
                  }`}
                >
                  {modelsAllAgree ? (
                    <>
                      <Check className="w-3.5 h-3.5" /> All 3 models agree
                    </>
                  ) : (
                    "Models disagree"
                  )}
                </span>
              </div>
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                {modelPill("RF", "Random Forest", agreement.rf)}
                {modelPill("NB", "Naive Bayes", agreement.nb)}
                {modelPill("SVM", "SVM", agreement.svm)}
              </div>
              <p className="text-xs text-muted-foreground mt-3">
                {modelsAllAgree
                  ? "All 3 models agree — strong support for this result."
                  : "Models disagree — lower confidence in this result."}
              </p>
            </div>
          </div>
        </div>
      )}

      {/* Section 4 — Alternative Possibilities */}
      {predictions.length > 1 && (
        <div className="bg-white rounded-2xl shadow-lg p-6 md:p-8 border border-border print:shadow-none print:rounded-none report-card">
          <button
            type="button"
            onClick={() => setShowAlternatives(v => !v)}
            className="w-full flex items-center justify-between gap-3 text-left"
            aria-expanded={showAlternatives}
          >
            <div className="flex items-center gap-3">
              <div className="w-10 h-10 bg-primary/10 rounded-xl flex items-center justify-center flex-shrink-0">
                <Activity className="w-5 h-5 text-primary" />
              </div>
              <div>
                <h3 className="text-lg font-bold text-foreground">Other possibilities to consider</h3>
                <p className="text-xs text-muted-foreground">
                  {predictions.length - 1} alternate condition(s) with overlapping symptoms
                </p>
              </div>
            </div>
            <ChevronDown
              className={`w-5 h-5 text-muted-foreground transition-transform duration-200 flex-shrink-0 ${
                showAlternatives ? "rotate-180" : ""
              }`}
            />
          </button>

          {showAlternatives && (
            <div className="mt-5 space-y-4">
              {predictions.slice(1).map(pred => {
                const p = parseFloat(pct(pred.confidence));
                const pBand = deriveBand(pred.confidence);
                return (
                  <div
                    key={pred.disease}
                    className="rounded-xl border border-gray-200 bg-gray-50 p-4"
                  >
                    <div className="flex items-center justify-between gap-3 mb-2">
                      <p className="text-base font-bold text-foreground">{pred.disease}</p>
                      <span className="text-lg font-bold text-foreground shrink-0">
                        {pct(pred.confidence)}%
                      </span>
                    </div>
                    <ConfidenceBar percent={p} band={pBand} small />
                  </div>
                );
              })}
              <p className="text-xs italic text-muted-foreground bg-amber-50 border border-amber-200 rounded-lg px-3 py-2">
                These are not diagnoses — they are other conditions that share similar symptoms
                and cannot be ruled out from this assessment alone.
              </p>
            </div>
          )}
        </div>
      )}

      {/* Section 5 — Guardrail Flags */}
      {flags.length > 0 && (
        <div className="bg-white rounded-2xl shadow-lg p-6 md:p-8 border border-border print:shadow-none print:rounded-none report-card">
          <SectionHeader
            icon={<ShieldAlert className="w-5 h-5 text-primary" />}
            title="Assessment Caveats"
          />
          <div className="space-y-3">
            {flags.map(flag => {
              const t = FLAG_TRANSLATIONS[flag];
              const toneClass =
                t.icon === "danger"
                  ? "border-red-200 bg-red-50"
                  : t.icon === "warn"
                  ? "border-amber-200 bg-amber-50"
                  : "border-blue-200 bg-blue-50";
              const iconClass =
                t.icon === "danger"
                  ? "text-red-600"
                  : t.icon === "warn"
                  ? "text-amber-600"
                  : "text-blue-600";
              return (
                <div key={flag} className={`flex gap-3 rounded-xl border p-4 ${toneClass}`}>
                  <div className="w-8 h-8 rounded-lg bg-white/70 flex items-center justify-center flex-shrink-0">
                    {t.icon === "danger" ? (
                      <AlertTriangle className={`w-5 h-5 ${iconClass}`} />
                    ) : t.icon === "warn" ? (
                      <AlertTriangle className={`w-5 h-5 ${iconClass}`} />
                    ) : (
                      <Info className={`w-5 h-5 ${iconClass}`} />
                    )}
                  </div>
                  <div>
                    <p className="text-sm font-bold text-foreground">{t.title}</p>
                    <p className="text-sm text-muted-foreground mt-0.5">{t.text}</p>
                  </div>
                </div>
              );
            })}
          </div>
          {result.confusable_with_note && (
            <div className="mt-4 flex gap-3 rounded-xl border border-amber-200 bg-amber-50 p-4">
              <Info className="w-5 h-5 text-amber-600 flex-shrink-0 mt-0.5" />
              <p className="text-sm text-amber-900">
                <span className="font-bold">Note:</span> {result.confusable_with_note}
              </p>
            </div>
          )}
        </div>
      )}

      {/* Section 6 — Recommended Precautions */}
      {top && top.precautions && top.precautions.length > 0 && (
        <div className="bg-white rounded-2xl shadow-lg p-6 md:p-8 border border-border print:shadow-none print:rounded-none report-card">
          <SectionHeader
            icon={<CheckCircle2 className="w-5 h-5 text-primary" />}
            title={`Recommended Precautions for ${top.disease}`}
          />
          {band === "LOW" && (
            <div className="mb-4 flex gap-3 rounded-xl border border-orange-200 bg-orange-50 p-4">
              <AlertTriangle className="w-5 h-5 text-orange-600 flex-shrink-0 mt-0.5" />
              <p className="text-sm text-orange-900">
                These precautions are for {top.disease}. Since confidence is low, verify the
                diagnosis before following these steps.
              </p>
            </div>
          )}
          {emergency && (
            <div className="mb-4 flex gap-3 rounded-xl border border-red-200 bg-red-50 p-4">
              <AlertTriangle className="w-5 h-5 text-red-600 flex-shrink-0 mt-0.5" />
              <p className="text-sm text-red-900">
                Seek emergency care first. These are general precautions only.
              </p>
            </div>
          )}
          <ol className="space-y-3">
            {top.precautions.map((pc, i) => (
              <li key={`${pc}-${i}`} className="flex items-start gap-3">
                <span className="w-7 h-7 rounded-full bg-primary/10 text-primary text-sm font-bold flex items-center justify-center flex-shrink-0">
                  {i + 1}
                </span>
                <CheckCircle2 className="w-5 h-5 text-green-600 flex-shrink-0 mt-1" />
                <span className="text-sm leading-relaxed text-foreground">{pc}</span>
              </li>
            ))}
          </ol>
        </div>
      )}

      {/* Section 7 — Disclaimer Footer */}
      <div className="bg-gray-50 rounded-2xl border border-border p-5 md:p-6 report-card">
        <p className="text-xs italic text-muted-foreground leading-relaxed">
          {result.disclaimer || "This is a screening aid, not a medical diagnosis."}
        </p>
        <p className="text-xs text-muted-foreground mt-3">
          This report was generated on{" "}
          {new Date().toLocaleString("en-US", {
            year: "numeric",
            month: "long",
            day: "numeric",
            hour: "numeric",
            minute: "2-digit",
          })}
        </p>
        <p className="text-xs text-muted-foreground mt-1">
          Powered by RF + Naive Bayes + SVM ensemble (40-symptom model)
        </p>
      </div>

      {/* Section 8 — Action Buttons */}
      <div className="flex flex-col sm:flex-row gap-4 pb-4 print:hidden report-actions">
        <button
          type="button"
          onClick={() => window.print()}
          className="flex-1 flex items-center justify-center gap-2 px-6 py-3 border-2 border-primary text-primary font-semibold rounded-lg hover:bg-primary/5 transition-colors"
        >
          <Printer className="w-4 h-4" />
          Print / Save as PDF
        </button>
        <button
          type="button"
          onClick={onStartNew}
          className="flex-1 flex items-center justify-center gap-2 px-6 py-3 bg-primary text-primary-foreground font-semibold rounded-lg hover:bg-primary/90 transition-colors shadow-lg"
        >
          <Plus className="w-4 h-4" />
          Start New Assessment
        </button>
      </div>
    </div>
  );
}