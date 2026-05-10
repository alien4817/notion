import crypto from "node:crypto";

import type { DrugShortageRecord } from "./types.js";

type HashInput = Pick<
  DrugShortageRecord,
  "drug_name" | "ingredient" | "license_no" | "shortage_status" | "alternative_drug" | "updated_at"
>;

type DrugKeyInput = Pick<DrugShortageRecord, "drug_name" | "ingredient" | "license_no">;

function sha256(value: string) {
  return crypto.createHash("sha256").update(value).digest("hex");
}

export function createDrugKey(record: DrugKeyInput) {
  return sha256(record.drug_name + record.ingredient + record.license_no);
}

export function createRecordHash(record: HashInput) {
  const rawValue =
    record.drug_name +
    record.ingredient +
    record.license_no +
    record.shortage_status +
    record.alternative_drug +
    record.updated_at;

  return sha256(rawValue);
}

export function createHashId(record: HashInput) {
  return createRecordHash(record);
}
