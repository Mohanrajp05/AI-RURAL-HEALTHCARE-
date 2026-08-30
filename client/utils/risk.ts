// Risk-level classification helpers.
//
// The CANONICAL implementation lives in backend/risk_classification.py
// (compute_risk_level). The backend returns "risk_level" in every prediction
// response and stored records are saved with it.
//
// This mirror exists ONLY to backfill legacy assessment records already in
// localStorage (saved before the risk_level field existed). The rules must
// stay identical to the backend function.

export const EMERGENCY_DISEASES = new Set(["Heart attack", "Paralysis (brain hemorrhage)"]);

export const HIGH_SEVERITY_DISEASES = new Set([
  "Heart attack",
  "Paralysis (brain hemorrhage)",
  "Tuberculosis",
  "Pneumonia",
  "Dengue",
  "Typhoid",
  "AIDS",
  "Hepatitis B",
  "Hepatitis C",
  "Hepatitis D",
  "Hepatitis E",
  "hepatitis A",
  "Chronic cholestasis",
  "Alcoholic hepatitis",
  "Jaundice",
  "Diabetes",
  "Hypertension",
]);

export const MEDIUM_SEVERITY_DISEASES = new Set([
  "Malaria",
  "Bronchial Asthma",
  "Gastroenteritis",
  "GERD",
  "Peptic ulcer diseae",
  "Hyperthyroidism",
  "Hypothyroidism",
  "Hypoglycemia",
  "Migraine",
  "Arthritis",
  "Osteoarthristis",
  "Cervical spondylosis",
  "Urinary tract infection",
  "(vertigo) Paroymsal  Positional Vertigo",
]);

export const LOW_SEVERITY_DISEASES = new Set([
  "Common Cold",
  "Allergy",
  "Acne",
  "Fungal infection",
  "Impetigo",
  "Psoriasis",
  "Drug Reaction",
  "Chicken pox",
  "Dimorphic hemmorhoids(piles)",
  "Varicose veins",
]);

export type RiskLevel = "Low Risk" | "Medium Risk" | "High Risk";

function normalizeConfidence(confidence: number): number {
  const c = Number(confidence ?? 0);
  if (Number.isNaN(c)) return 0;
  const value = c > 1 ? c / 100 : c;
  return Math.max(0, Math.min(1, value));
}

export function computeRiskLevel(
  disease: string,
  confidence: number,
  emergencyAlert = false
): RiskLevel {
  const name = String(disease || "").trim();
  const conf = normalizeConfidence(confidence);

  // Rule 1: Emergency diseases are ALWAYS High Risk, regardless of confidence.
  if (EMERGENCY_DISEASES.has(name) || emergencyAlert) return "High Risk";

  // Rule 2: High-severity disease + high confidence = High Risk.
  if (HIGH_SEVERITY_DISEASES.has(name) && conf >= 0.7) return "High Risk";

  // Rule 3: High-severity disease + medium/low confidence = Medium Risk.
  if (HIGH_SEVERITY_DISEASES.has(name)) return "Medium Risk";

  // Rule 4: Medium-severity disease + high confidence = Medium Risk.
  if (MEDIUM_SEVERITY_DISEASES.has(name) && conf >= 0.7) return "Medium Risk";

  // Rule 5: Medium-severity disease + low confidence = Low Risk.
  if (MEDIUM_SEVERITY_DISEASES.has(name)) return "Low Risk";

  // Rule 6: Low-severity disease = always Low Risk regardless of confidence.
  if (LOW_SEVERITY_DISEASES.has(name)) return "Low Risk";

  // Fallback: unknown severity -> Medium Risk (safer than Low).
  return "Medium Risk";
}

export type SeverityTier = "emergency" | "high" | "medium" | "low" | "unknown";

/** Return the severity tier for a disease name (mirrors backend severity_tier). */
export function severityTier(disease: string): SeverityTier {
  const name = String(disease || "").trim();
  if (EMERGENCY_DISEASES.has(name)) return "emergency";
  if (HIGH_SEVERITY_DISEASES.has(name)) return "high";
  if (MEDIUM_SEVERITY_DISEASES.has(name)) return "medium";
  if (LOW_SEVERITY_DISEASES.has(name)) return "low";
  return "unknown";
}

/**
 * Always-visible caption shown directly under the risk badge. It explains WHY a
 * given risk level applies to THIS disease. Regenerated dynamically from
 * severity tier + confidence so it never needs per-disease manual updates.
 */
export function riskCaption(
  disease: string,
  confidence: number,
  emergencyAlert = false
): string {
  const name = String(disease || "").trim();
  const conf = normalizeConfidence(confidence);
  const tier = severityTier(name);

  if (tier === "emergency" || emergencyAlert) {
    return "High Risk — always treated as an emergency, regardless of prediction certainty.";
  }
  if (tier === "high") {
    return conf >= 0.7
      ? "High Risk — a serious condition and the model is highly certain."
      : "Medium Risk — a serious condition, but the model is less certain.";
  }
  if (tier === "medium") {
    return conf >= 0.7
      ? "Medium Risk — a manageable, non-emergency condition, even when the model is highly certain."
      : "Low Risk — a manageable, non-emergency condition, but the model is less certain.";
  }
  if (tier === "low") {
    return "Low Risk — a mild condition with low medical urgency.";
  }
  return "Medium Risk — a safe default when the condition is not fully recognised.";
}

export interface RiskBackfillRecord {
  predicted_disease?: string;
  confidence?: number;
  emergency_alert?: boolean;
  risk_level?: string;
}

/** True when a legacy record is missing a valid risk_level and needs backfill. */
export function needsRiskBackfill(record: RiskBackfillRecord): boolean {
  const level = record.risk_level;
  return level !== "Low Risk" && level !== "Medium Risk" && level !== "High Risk";
}

/**
 * In-place migration for localStorage assessment records: recomputes
 * risk_level from disease + confidence + emergency_alert using the CURRENT
 * rule set and overwrites the stored value whenever it differs.
 *
 * This must run unconditionally for every record, not only ones that are
 * missing risk_level (needsRiskBackfill). A record can already hold a
 * VALID-LOOKING risk_level string that was computed under an OLDER version
 * of computeRiskLevel's rules (e.g. before a disease moved severity tiers,
 * or the confidence threshold changed) -- that value is stale, not absent,
 * and skipping it would leave it wrong forever. Safe/idempotent to call on
 * every page load: correct records are left untouched (not counted).
 *
 * Returns the number of records actually changed.
 */
export function backfillRiskLevels<T extends RiskBackfillRecord>(records: T[]): number {
  let updated = 0;
  for (const record of records) {
    const disease = record.predicted_disease ?? "";
    const confidence = Number(record.confidence ?? 0);
    const emergencyAlert = !!record.emergency_alert;
    const recomputed = computeRiskLevel(disease, confidence, emergencyAlert);
    if (record.risk_level !== recomputed) {
      record.risk_level = recomputed;
      updated += 1;
    }
  }
  return updated;
}
