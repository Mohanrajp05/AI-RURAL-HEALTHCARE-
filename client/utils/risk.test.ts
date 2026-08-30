import { describe, expect, it } from "vitest";
import {
  backfillRiskLevels,
  computeRiskLevel,
  needsRiskBackfill,
  type RiskBackfillRecord,
} from "@/utils/risk";

// Explicit element type for the legacy-record fixtures below -- without it,
// TS infers each `records` array literal's element type as the union of its
// individual objects' own shapes (since some omit risk_level/emergency_alert),
// and indexing into that union then rejects `.risk_level` on the branches
// that didn't include it, even though RiskBackfillRecord declares it optional.
type TestRecord = RiskBackfillRecord & { id: number };

describe("computeRiskLevel", () => {
  it("flags emergency diseases as High Risk regardless of confidence", () => {
    expect(computeRiskLevel("Heart attack", 0.966)).toBe("High Risk");
    expect(computeRiskLevel("Heart attack", 0.4)).toBe("High Risk");
    expect(computeRiskLevel("Paralysis (brain hemorrhage)", 0.1)).toBe("High Risk");
  });

  it("flags high-severity diseases as High Risk only at high confidence", () => {
    expect(computeRiskLevel("Diabetes", 0.75)).toBe("High Risk");
    expect(computeRiskLevel("Tuberculosis", 0.55)).toBe("Medium Risk");
    expect(computeRiskLevel("Pneumonia", 0.3)).toBe("Medium Risk");
  });

  it("flags medium-severity diseases by confidence", () => {
    expect(computeRiskLevel("Malaria", 0.8)).toBe("Medium Risk");
    expect(computeRiskLevel("Malaria", 0.45)).toBe("Low Risk");
    expect(computeRiskLevel("Migraine", 0.635)).toBe("Low Risk");
  });

  it("keeps low-severity diseases Low Risk even at 99% confidence", () => {
    expect(computeRiskLevel("Chicken pox", 0.99)).toBe("Low Risk");
    expect(computeRiskLevel("Common Cold", 0.99)).toBe("Low Risk");
    expect(computeRiskLevel("Acne", 0.9)).toBe("Low Risk");
  });

  it("defaults unknown diseases to Medium Risk", () => {
    expect(computeRiskLevel("Unknown Disease", 0.9)).toBe("Medium Risk");
  });

  it("normalizes percentage confidence", () => {
    expect(computeRiskLevel("Diabetes", 75)).toBe("High Risk");
    expect(computeRiskLevel("Chicken pox", 63.5)).toBe("Low Risk");
  });
});

describe("backfillRiskLevels (legacy stored-record migration)", () => {
  it("updates legacy records lacking risk_level in place", () => {
    const records: TestRecord[] = [
      {
        id: 1,
        predicted_disease: "Heart attack",
        confidence: 96.6,
        emergency_alert: true,
      },
      { id: 2, predicted_disease: "Chicken pox", confidence: 63.5 },
      { id: 3, predicted_disease: "Diabetes", confidence: 75 },
    ];
    expect(records.every((r) => needsRiskBackfill(r))).toBe(true);

    const updated = backfillRiskLevels(records);

    expect(updated).toBe(3);
    expect(records[0].risk_level).toBe("High Risk");
    expect(records[1].risk_level).toBe("Low Risk");
    expect(records[2].risk_level).toBe("High Risk");
  });

  it("does not touch records whose stored risk_level already matches current rules", () => {
    const records: TestRecord[] = [
      { id: 1, predicted_disease: "Heart attack", confidence: 96.6, risk_level: "High Risk" },
      { id: 2, predicted_disease: "Chicken pox", confidence: 63.5 },
    ];
    const updated = backfillRiskLevels(records);
    expect(updated).toBe(1);
    expect(records[0].risk_level).toBe("High Risk");
    expect(records[1].risk_level).toBe("Low Risk");
  });

  it("corrects stale records whose stored risk_level no longer matches current rules", () => {
    // Regression case: same disease + same confidence stored under an older
    // classification rule must not be left mismatched against a newer one
    // just because the old value happens to be a "valid" risk level string.
    const records: TestRecord[] = [
      {
        id: 1,
        predicted_disease: "(vertigo) Paroymsal  Positional Vertigo",
        confidence: 95.9,
        risk_level: "High Risk", // stale -- computed under an older rule set
      },
      {
        id: 2,
        predicted_disease: "(vertigo) Paroymsal  Positional Vertigo",
        confidence: 95.9,
        risk_level: "Medium Risk", // already matches current rules
      },
    ];
    const updated = backfillRiskLevels(records);
    expect(updated).toBe(1);
    expect(records[0].risk_level).toBe("Medium Risk");
    expect(records[1].risk_level).toBe("Medium Risk");
  });
});
