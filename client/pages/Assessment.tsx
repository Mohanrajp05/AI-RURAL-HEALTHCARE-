import { Layout } from "@/components/Layout";
import AssessmentReport, {
  AssessmentReportData,
  ReportModelAgreement,
} from "@/components/AssessmentReport";
import { useRef, useState } from "react";
import {
  AlertCircle,
  Brain,
  Loader2,
  Search,
  X,
} from "lucide-react";
import newCheckboxLabels from "@/new_checkbox_labels.json";

const BACKEND = import.meta.env.VITE_BACKEND_URL || "http://127.0.0.1:5001";

interface SymptomOption {
  value: string;
  label: string;
}

interface PredictionEntry {
  disease: string;
  confidence: number;
  precautions?: string[];
}

interface AssessmentResult {
  predicted_disease: string;
  confidence: number;
  risk_category: string;
  risk_score: number;
  risk_level: string;
  vitals_analysis: {
    bp: string;
    heart_rate: string;
    temperature: string;
    sugar: string;
  };
  recommendation: string;
  combined_symptom_count?: number;
  ai_detected_symptoms?: string[];
  predictions?: PredictionEntry[];
  confidence_band?: "HIGH" | "MEDIUM" | "LOW";
  top1_vs_top2_margin?: number;
  model_agreement?: ReportModelAgreement;
  flags?: string[];
  emergency_alert?: boolean;
  confusable_with_note?: string | null;
  guarded_recommendation?: string;
  matched_symptoms?: string[];
  ignored_checkboxes?: string[];
  disclaimer?: string;
}

export default function Assessment() {
  const [formData, setFormData] = useState({
    patientName: "",
    age: "",
    gender: "",
    bloodPressureSystolic: "",
    bloodPressureDiastolic: "",
    heartRate: "",
    temperature: "",
    sugarLevel: "",
    symptoms: [] as string[],
  });

  const [result, setResult] = useState<AssessmentResult | null>(null);
  
  const [loading, setLoading] = useState(false);
  const [showResult, setShowResult] = useState(false);

  const [symptomText, setSymptomText] = useState("");
  const [aiSymptoms, setAiSymptoms] = useState<string[]>([]);
  const [mappingLoading, setMappingLoading] = useState(false);
  const [mappingError, setMappingError] = useState("");

  const [searchQuery, setSearchQuery] = useState("");
  const searchInputRef = useRef<HTMLInputElement>(null);
  const symptomGridRef = useRef<HTMLDivElement>(null);
  const reportRef = useRef<HTMLDivElement>(null);

  const MAX_SYMPTOM_TEXT = 300;

  // value = EXACT string predict_guarded() expects; label = friendly UI text.
  // "Increased Thirst" and "High Blood Pressure" were removed — they have no
  // equivalent in the new 40-symptom vocabulary (previously mis-mapped).
  const baseSymptomOptions: SymptomOption[] = [
    { value: "Mild Fever", label: "Mild Fever" },
    { value: "High Fever", label: "High Fever" },
    { value: "Cough", label: "Cough" },
    { value: "Runny Nose", label: "Runny Nose" },
    { value: "Breathlessness", label: "Shortness of Breath" },
    { value: "Fatigue", label: "Fatigue" },
    { value: "Headache", label: "Headache" },
    { value: "Muscle Pain", label: "Body Aches / Muscle Pain" },
    { value: "Throat Irritation", label: "Sore Throat" },
    { value: "Nausea", label: "Nausea" },
    { value: "Vomiting", label: "Vomiting" },
    { value: "Diarrhoea", label: "Diarrhea" },
    { value: "Loss of Appetite", label: "Loss of Appetite" },
    { value: "Chest Pain", label: "Chest Pain" },
    { value: "Chills", label: "Chills" },
    { value: "Dizziness", label: "Dizziness" },
    { value: "Joint Pain", label: "Joint Pain" },
    { value: "Skin Rash", label: "Skin Rash" },
    { value: "Polyuria", label: "Frequent Urination" },
    { value: "Blurred and Distorted Vision", label: "Blurred Vision" },
  ];

  // internal id (key) -> exact predict_guarded() checkbox string (value).
  const NEW_CHECKBOX_CANONICAL: Record<string, string> = {
    abdominal_pain: "Abdominal Pain",
    yellowish_skin: "Yellowish Skin",
    yellowing_of_eyes: "Yellowing of Eyes",
    malaise: "Malaise",
    itching: "Itching",
    sweating: "Sweating",
    dark_urine: "Dark Urine",
    irritability: "Irritability",
    excessive_hunger: "Excessive Hunger",
    weight_loss: "Weight Loss",
    lethargy: "Lethargy",
    phlegm: "Phlegm",
    swelled_lymph_nodes: "Swelled Lymph Nodes",
    loss_of_balance: "Loss of Balance",
    abnormal_menstruation: "Abnormal Menstruation",
    muscle_weakness: "Muscle Weakness",
    depression: "Depression",
    fast_heart_rate: "Fast Heart Rate",
    red_spots_over_body: "Red Spots Over Body",
    back_pain: "Back Pain",
    visual_disturbances: "Sensitivity to Light",
    wheezing: "Wheezing",
    joint_pain: "Severe Joint Pain",
    "dischromic _patches": "Pale / Clay-coloured Stools",
  };

  const newSymptomOptions: SymptomOption[] = Object.entries(
    newCheckboxLabels as Record<string, string>
  ).map(([id, label]) => ({
    value: NEW_CHECKBOX_CANONICAL[id] ?? id,
    label,
  }));

  const availableSymptoms: SymptomOption[] = [
    ...baseSymptomOptions,
    ...newSymptomOptions,
  ];

  const trimmedSearchQuery = searchQuery.trim().toLowerCase();
  const searchActive = trimmedSearchQuery.length > 0;
  const matchesQuery = (label: string) => label.toLowerCase().includes(trimmedSearchQuery);

  const checkedSymptomSet = new Set(formData.symptoms);
  const matchedUncheckedCount = searchActive
    ? availableSymptoms.filter(o => !checkedSymptomSet.has(o.value) && matchesQuery(o.label)).length
    : 0;
  const visibleSymptomCount = searchActive
    ? formData.symptoms.length + matchedUncheckedCount
    : availableSymptoms.length;
  const noMatches = searchActive && matchedUncheckedCount === 0;

  const highlightMatch = (label: string): React.ReactNode => {
    if (!searchActive) return label;
    const idx = label.toLowerCase().indexOf(trimmedSearchQuery);
    if (idx === -1) return label;
    const matchLen = searchQuery.trim().length;
    return (
      <>
        {label.slice(0, idx)}
        <mark className="symptom-mark">{label.slice(idx, idx + matchLen)}</mark>
        {label.slice(idx + matchLen)}
      </>
    );
  };

  const handleSearchKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Escape") {
      setSearchQuery("");
      e.currentTarget.focus();
    } else if (e.key === "ArrowDown") {
      const firstVisible = symptomGridRef.current?.querySelector<HTMLElement>('[data-visible="true"] input');
      firstVisible?.focus();
    }
  };

  const handleInputChange = (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => {
    const { name, value } = e.target;
    setFormData(prev => ({
      ...prev,
      [name]: value,
    }));
  };

  const toggleSymptom = (symptom: string) => {
    setFormData(prev => ({
      ...prev,
      symptoms: prev.symptoms.includes(symptom)
        ? prev.symptoms.filter(s => s !== symptom)
        : [...prev.symptoms, symptom],
    }));
  };

  const handleMapSymptoms = async () => {
    const text = symptomText.trim();
    if (!text) {
      setMappingError("Please describe your symptoms in the box above.");
      return;
    }

    setMappingLoading(true);
    setMappingError("");

    try {
      const response = await fetch(`${BACKEND}/map-symptoms`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: text.slice(0, MAX_SYMPTOM_TEXT) }),
      });
      const data = await response.json();

      if (!response.ok) {
        throw new Error(data.error || "Mapping failed");
      }

      setAiSymptoms(data.tokens || []);
      if ((data.tokens || []).length === 0) {
        setMappingError(
          "No symptoms detected. Try describing them differently (e.g. \"deep stomach pain\")."
        );
      }
    } catch (error) {
      console.error("Error mapping symptoms:", error);
      setMappingError(
        "Could not map symptoms. Please ensure Flask and Ollama are running on http://127.0.0.1:5001"
      );
      setAiSymptoms([]);
    } finally {
      setMappingLoading(false);
    }
  };

  const removeAiSymptom = (symptom: string) => {
    setAiSymptoms(prev => prev.filter(s => s !== symptom));
  };

  

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);

    try {
      const payload = new FormData();
      // Attach the logged-in user's email (if any) so admin/doctor can see
      // who submitted this assessment. Guests submit without one.
      try {
        const storedUserForSubmit = localStorage.getItem("user");
        if (storedUserForSubmit) {
          const submittingUser = JSON.parse(storedUserForSubmit);
          if (submittingUser?.email) {
            payload.append("email", submittingUser.email);
          }
        }
      } catch {
        // Malformed localStorage entry -- submit without an email rather than fail.
      }
      payload.append("patientName", formData.patientName);
      payload.append("age", formData.age);
      payload.append("gender", formData.gender);
      payload.append("bloodPressureSystolic", formData.bloodPressureSystolic);
      payload.append("bloodPressureDiastolic", formData.bloodPressureDiastolic);
      payload.append("heartRate", formData.heartRate);
      payload.append("temperature", formData.temperature);
      payload.append("sugarLevel", formData.sugarLevel);
      // labTestResult removed — not collected
      payload.append("symptoms", JSON.stringify(formData.symptoms));
      payload.append("extra_symptoms", JSON.stringify(aiSymptoms));

      // No file upload option: payload only contains form fields and symptoms

      const response = await fetch(`${BACKEND}/predict`, {
        method: "POST",
        body: payload,
      });

      const data = await response.json();

      if (data.predicted_disease) {
        // If backend returns extracted fields, update formData so report is filled
        setFormData(prev => ({
          ...prev,
          patientName: data.patientName || prev.patientName,
          age: data.age || prev.age,
          gender: data.gender || prev.gender,
          bloodPressureSystolic: data.bloodPressureSystolic || prev.bloodPressureSystolic,
          bloodPressureDiastolic: data.bloodPressureDiastolic || prev.bloodPressureDiastolic,
          heartRate: data.heartRate || prev.heartRate,
          temperature: data.temperature || prev.temperature,
          sugarLevel: data.sugarLevel || prev.sugarLevel,
          // labTestResult removed
          symptoms: data.symptoms || prev.symptoms,
        }));

        const assessmentResult: AssessmentResult = {
          predicted_disease: data.predicted_disease,
          confidence: data.confidence ?? 0,
          risk_category: data.risk_category ?? "Unknown",
          risk_score: data.risk_score ?? 0,
          risk_level: data.risk_level ?? "Unknown",
          vitals_analysis: data.vitals_analysis ?? { bp: "N/A", heart_rate: "N/A", temperature: "N/A", sugar: "N/A" },
          recommendation: data.recommendation || "Consult a healthcare provider for detailed assessment.",
          combined_symptom_count: data.combined_symptom_count ?? 0,
          ai_detected_symptoms: data.ai_detected_symptoms ?? [],
          predictions: data.predictions ?? [],
          confidence_band: data.confidence_band ?? "",
          top1_vs_top2_margin: data.top1_vs_top2_margin ?? 0,
          model_agreement: data.model_agreement ?? undefined,
          flags: data.flags ?? [],
          emergency_alert: data.emergency_alert ?? false,
          confusable_with_note: data.confusable_with_note ?? null,
          guarded_recommendation: data.guarded_recommendation ?? "",
          matched_symptoms: data.matched_symptoms ?? [],
          ignored_checkboxes: data.ignored_checkboxes ?? [],
          disclaimer: data.disclaimer ?? "",
        };
        setResult(assessmentResult);
        setShowResult(true);

        setTimeout(() => {
          reportRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
        }, 120);

        // Save to user's assessment history if logged in
        const storedUser = localStorage.getItem("user");
        if (storedUser) {
          const user = JSON.parse(storedUser);
          const key = `assessments_${user.email}`;
          const existing = JSON.parse(localStorage.getItem(key) || "[]");
          const newRecord = {
            id: Date.now(),
            date: new Date().toISOString(),
            patientName: data.patientName || formData.patientName,
            age: data.age || formData.age,
            gender: data.gender || formData.gender,
            symptoms: data.symptoms || formData.symptoms,
            vitals: {
              bp: `${data.bloodPressureSystolic || formData.bloodPressureSystolic}/${data.bloodPressureDiastolic || formData.bloodPressureDiastolic}`,
              heartRate: data.heartRate || formData.heartRate,
              temperature: data.temperature || formData.temperature,
              sugar: data.sugarLevel || formData.sugarLevel,
            },
            ...assessmentResult,
          };
          localStorage.setItem(key, JSON.stringify([newRecord, ...existing]));
        }
      } else {
        throw new Error(data.error || "Prediction failed");
      }
    } catch (error) {
      console.error("Error calling ML API:", error);
      alert(
        "Failed to get prediction. Please ensure the Flask backend is running on http://127.0.0.1:5001"
      );
    } finally {
      setLoading(false);
    }
  };

  // Allow submit when all required fields are filled
  const isFormValid = (
    formData.patientName &&
    formData.age &&
    formData.gender &&
    formData.bloodPressureSystolic &&
    formData.bloodPressureDiastolic &&
    formData.heartRate &&
    formData.temperature &&
    formData.sugarLevel &&
    formData.symptoms.length > 0
  );

  const resetAssessment = () => {
    setFormData({
      patientName: "",
      age: "",
      gender: "",
      bloodPressureSystolic: "",
      bloodPressureDiastolic: "",
      heartRate: "",
      temperature: "",
      sugarLevel: "",
      symptoms: [],
    });
    setResult(null);
    setShowResult(false);
    setAiSymptoms([]);
    setSymptomText("");
    setMappingError("");
    setSearchQuery("");
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  return (
    <Layout>
      <style>{`
        @page {
          size: A4;
          margin: 0.5in;
        }
        @media print {
          * { box-sizing: border-box; }
          html, body { margin: 0; padding: 0; background: white; }
          .max-w-4xl { max-width: 100%; }
          .px-4 { padding-left: 0.5rem; padding-right: 0.5rem; }
          .sm\\:px-6 { padding-left: 0.5rem; padding-right: 0.5rem; }
          .lg\\:px-8 { padding-left: 0.5rem; padding-right: 0.5rem; }
          .assessment-report { page-break-after: avoid; }
          .report-section { page-break-inside: avoid; margin-bottom: 0.5rem; padding: 1rem !important; }
          .print-grid { display: grid; gap: 0.75rem; grid-template-columns: 1fr !important; }
          .grid { display: grid; grid-template-columns: 1fr !important; gap: 0.75rem; }
          .rounded-2xl { border-radius: 0.5rem; }
          .p-6 { padding: 1rem; }
          .p-8 { padding: 1rem; }
          .md\\:p-8 { padding: 1rem; }
          .text-2xl { font-size: 1.25rem; }
          .text-3xl { font-size: 1.5rem; }
          .text-lg { font-size: 1rem; }
          h1 { font-size: 1.5rem; }
          h3 { font-size: 1rem; }
          .gap-4 { gap: 0.75rem; }
          .mb-6 { margin-bottom: 0.75rem; }
          .mb-5 { margin-bottom: 0.5rem; }
        }
        .symptom-mark {
          background: hsl(var(--primary) / 0.15);
          font-weight: 600;
          color: inherit;
          border-radius: 2px;
          padding: 0 1px;
        }
      `}</style>
      <div className="bg-gradient-to-br from-primary/5 via-accent/5 to-secondary/5 min-h-screen py-8 md:py-12 print:bg-white">
        <div className="max-w-4xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="mb-8">
            <h1 className="text-3xl md:text-4xl font-bold text-foreground mb-2">
              Health Assessment
            </h1>
            <p className="text-lg text-muted-foreground">
              Enter patient information to receive AI-powered health assessment
            </p>
          </div>

          {!showResult ? (
            <div className="bg-white rounded-2xl shadow-lg p-6 md:p-8 border border-border">
              <form onSubmit={handleSubmit} className="space-y-8">
                {/* Patient Name */}
                <div>
                  <label className="block text-sm font-semibold text-foreground mb-2">
                    Patient Name
                  </label>
                  <input
                    type="text"
                    name="patientName"
                    value={formData.patientName}
                    onChange={handleInputChange}
                    placeholder="Enter patient's full name"
                    className="w-full px-4 py-3 border border-border rounded-lg focus:outline-none focus:ring-2 focus:ring-primary text-foreground bg-white"
                    required
                  />
                </div>

                {/* Age and Gender */}
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  <div>
                    <label className="block text-sm font-semibold text-foreground mb-2">
                      Age (years)
                    </label>
                    <input
                      type="number"
                      name="age"
                      value={formData.age}
                      onChange={handleInputChange}
                      placeholder="Enter age"
                      className="w-full px-4 py-3 border border-border rounded-lg focus:outline-none focus:ring-2 focus:ring-primary text-foreground bg-white"
                      min="0"
                      max="150"
                      required
                    />
                  </div>
                  <div>
                    <label className="block text-sm font-semibold text-foreground mb-2">
                      Gender
                    </label>
                    <select
                      name="gender"
                      value={formData.gender}
                      onChange={handleInputChange}
                      className="w-full px-4 py-3 border border-border rounded-lg focus:outline-none focus:ring-2 focus:ring-primary text-foreground bg-white"
                      required
                    >
                      <option value="">Select gender</option>
                      <option value="Male">Male</option>
                      <option value="Female">Female</option>
                      <option value="Other">Other</option>
                    </select>
                  </div>
                </div>

                {/* Blood Pressure */}
                <div>
                  <label className="block text-sm font-semibold text-foreground mb-2">
                    Blood Pressure (mmHg)
                  </label>
                  <div className="grid grid-cols-2 gap-4">
                    <div>
                      <label className="block text-xs text-muted-foreground mb-1">Systolic</label>
                      <input
                        type="number"
                        name="bloodPressureSystolic"
                        value={formData.bloodPressureSystolic}
                        onChange={handleInputChange}
                        placeholder="e.g., 120"
                        className="w-full px-4 py-3 border border-border rounded-lg focus:outline-none focus:ring-2 focus:ring-primary text-foreground bg-white"
                        min="0"
                        required
                      />
                    </div>
                    <div>
                      <label className="block text-xs text-muted-foreground mb-1">Diastolic</label>
                      <input
                        type="number"
                        name="bloodPressureDiastolic"
                        value={formData.bloodPressureDiastolic}
                        onChange={handleInputChange}
                        placeholder="e.g., 80"
                        className="w-full px-4 py-3 border border-border rounded-lg focus:outline-none focus:ring-2 focus:ring-primary text-foreground bg-white"
                        min="0"
                        required
                      />
                    </div>
                  </div>
                </div>

                {/* Heart Rate */}
                <div>
                  <label className="block text-sm font-semibold text-foreground mb-2">
                    Heart Rate (bpm)
                  </label>
                  <input
                    type="number"
                    name="heartRate"
                    value={formData.heartRate}
                    onChange={handleInputChange}
                    placeholder="Enter heart rate"
                    className="w-full px-4 py-3 border border-border rounded-lg focus:outline-none focus:ring-2 focus:ring-primary text-foreground bg-white"
                    min="0"
                        required
                  />
                </div>

                {/* Temperature */}
                <div>
                  <label className="block text-sm font-semibold text-foreground mb-2">
                    Temperature (°F)
                  </label>
                  <input
                    type="number"
                    name="temperature"
                    value={formData.temperature}
                    onChange={handleInputChange}
                    placeholder="Enter temperature"
                    step="0.1"
                    className="w-full px-4 py-3 border border-border rounded-lg focus:outline-none focus:ring-2 focus:ring-primary text-foreground bg-white"
                    min="0"
                    required
                  />
                </div>

                {/* Sugar Level */}
                <div>
                  <label className="block text-sm font-semibold text-foreground mb-2">
                    Blood Sugar Level (mg/dL)
                  </label>
                  <input
                    type="number"
                    name="sugarLevel"
                    value={formData.sugarLevel}
                    onChange={handleInputChange}
                    placeholder="Enter fasting blood sugar level"
                    className="w-full px-4 py-3 border border-border rounded-lg focus:outline-none focus:ring-2 focus:ring-primary text-foreground bg-white"
                    min="0"
                    required
                  />
                </div>

                {/* Lab Test Result field removed */}

                {/* Medical report upload removed */}

                {/* Symptoms */}
                <div>
                  <div className="flex items-center justify-between mb-3">
                    <label className="block text-sm font-semibold text-foreground">
                      Symptoms (select all that apply)
                    </label>
                    <span className="text-xs font-semibold bg-primary/10 text-primary px-2.5 py-1 rounded-full">
                      {formData.symptoms.length} selected
                    </span>
                  </div>

                  <div className="flex items-center gap-3 mb-3">
                    <div className="relative flex-1">
                      <Search className="absolute left-3.5 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground pointer-events-none" />
                      <input
                        type="text"
                        ref={searchInputRef}
                        value={searchQuery}
                        onChange={e => setSearchQuery(e.target.value)}
                        onKeyDown={handleSearchKeyDown}
                        placeholder="Search symptoms e.g. fever, pain, cough..."
                        aria-label="Search symptoms"
                        className="w-full px-10 py-3 border border-border rounded-lg focus:outline-none focus:ring-2 focus:ring-primary text-foreground bg-white"
                      />
                      {searchQuery.length > 0 && (
                        <button
                          type="button"
                          onClick={() => {
                            setSearchQuery("");
                            searchInputRef.current?.focus();
                          }}
                          aria-label="Clear search"
                          className="absolute right-2 top-1/2 -translate-y-1/2 w-8 h-8 flex items-center justify-center rounded-full text-muted-foreground hover:text-foreground hover:bg-gray-100 transition-colors"
                        >
                          <X className="w-4 h-4" />
                        </button>
                      )}
                    </div>
                    {searchActive && (
                      <span className="text-xs text-muted-foreground whitespace-nowrap shrink-0">
                        showing {visibleSymptomCount} of {availableSymptoms.length} symptoms
                      </span>
                    )}
                  </div>

                  {noMatches && (
                    <p className="text-sm text-muted-foreground mb-3">
                      No symptoms found for &quot;{searchQuery.trim()}&quot;
                    </p>
                  )}

                  <div
                    ref={symptomGridRef}
                    className="grid grid-cols-1 sm:grid-cols-2 gap-3"
                  >
                    {searchActive && formData.symptoms.length > 0 && (
                      <div
                        className="flex items-center gap-3 sm:col-span-2"
                        style={{ order: 0 }}
                      >
                        <span className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                          Selected
                        </span>
                        <div className="flex-1 h-px bg-border" />
                      </div>
                    )}
                    {availableSymptoms.map(option => {
                      const isChecked = checkedSymptomSet.has(option.value);
                      const isMatch = searchActive && matchesQuery(option.label);
                      const isVisible = !searchActive || isChecked || isMatch;
                      return (
                        <label
                          key={option.value}
                          data-visible={isVisible ? "true" : "false"}
                          style={{
                            display: isVisible ? undefined : "none",
                            order: searchActive ? (isChecked ? 1 : 2) : undefined,
                          }}
                          className="flex items-center gap-3 p-3 border border-border rounded-lg cursor-pointer hover:bg-primary/5 transition-colors"
                        >
                          <input
                            type="checkbox"
                            checked={formData.symptoms.includes(option.value)}
                            onChange={() => toggleSymptom(option.value)}
                            className="w-4 h-4 accent-primary rounded cursor-pointer"
                          />
                          <span className="text-foreground">
                            {isMatch ? highlightMatch(option.label) : option.label}
                          </span>
                        </label>
                      );
                    })}
                  </div>
                </div>

                {/* AI Natural Language Symptoms */}
                <div>
                  <label className="block text-sm font-semibold text-foreground mb-2">
                    Describe Other Symptoms (AI detects them for you)
                  </label>
                  <div className="flex flex-col sm:flex-row gap-3">
                    <input
                      type="text"
                      value={symptomText}
                      onChange={(e) => {
                        setSymptomText(e.target.value);
                        setMappingError("");
                      }}
                      maxLength={MAX_SYMPTOM_TEXT}
                      placeholder='e.g. "deep stomach pain, my eyes are turning yellow"'
                      className="flex-1 px-4 py-3 border border-border rounded-lg focus:outline-none focus:ring-2 focus:ring-primary text-foreground bg-white"
                    />
                    <button
                      type="button"
                      onClick={handleMapSymptoms}
                      disabled={mappingLoading || !symptomText.trim()}
                      className="flex items-center justify-center gap-2 px-5 py-3 bg-primary/10 text-primary font-semibold rounded-lg hover:bg-primary/20 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                    >
                      {mappingLoading ? (
                        <>
                          <Loader2 className="w-4 h-4 animate-spin" />
                          Detecting...
                        </>
                      ) : (
                        <>
                          <Brain className="w-4 h-4" />
                          Detect Symptoms
                        </>
                      )}
                    </button>
                  </div>
                  <div className="mt-2 flex items-center justify-between">
                    <p className="text-xs text-muted-foreground">
                      Describing in your own words helps the AI detect symptoms beyond the checklist.
                    </p>
                    <span className="text-xs text-muted-foreground">
                      {symptomText.length}/{MAX_SYMPTOM_TEXT}
                    </span>
                  </div>
                  {mappingError && (
                    <p className="mt-2 flex items-center gap-1.5 text-sm text-red-600">
                      <AlertCircle className="w-4 h-4" />
                      {mappingError}
                    </p>
                  )}
                  {aiSymptoms.length > 0 && (
                    <div className="mt-3 flex flex-wrap items-center gap-2">
                      <span className="text-sm font-semibold text-green-700">
                        AI Detected:
                      </span>
                      {aiSymptoms.map(symptom => (
                        <span
                          key={symptom}
                          className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-green-100 text-green-800 font-semibold text-sm rounded-full border border-green-200"
                        >
                          {symptom.replace(/_/g, " ")}
                          <button
                            type="button"
                            onClick={() => removeAiSymptom(symptom)}
                            className="w-4 h-4 flex items-center justify-center rounded-full text-green-700 hover:bg-green-200 transition-colors"
                            aria-label={`Remove ${symptom}`}
                          >
                            ×
                          </button>
                        </span>
                      ))}
                    </div>
                  )}
                </div>

                {/* Submit Button */}
                <div className="flex gap-4 pt-6">
                  <button
                    type="submit"
                    disabled={!isFormValid || loading}
                    className="flex-1 flex items-center justify-center gap-2 px-6 py-3 bg-primary text-primary-foreground font-semibold rounded-lg hover:bg-primary/90 transition-colors disabled:opacity-50 disabled:cursor-not-allowed shadow-lg hover:shadow-xl"
                  >
                    {loading ? (
                      <>
                        <Loader2 className="w-4 h-4 animate-spin" />
                        Analyzing...
                      </>
                    ) : (
                      "Get Assessment"
                    )}
                  </button>
                  <button
                    type="reset"
                    onClick={resetAssessment}
                    className="px-6 py-3 border-2 border-primary text-primary font-semibold rounded-lg hover:bg-primary/5 transition-colors"
                  >
                    Clear
                  </button>
                </div>
              </form>
            </div>
          ) : result ? (
            <div ref={reportRef}>
              <AssessmentReport
                result={result as AssessmentReportData}
                patient={formData}
                onStartNew={resetAssessment}
              />
            </div>
          ) : null}      </div>
      </div>
    </Layout>
  );
}
