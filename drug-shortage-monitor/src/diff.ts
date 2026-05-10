import type { DiffResult, DrugShortageRecord, Snapshot } from "./types.js";

export function diffRecords(currentRecords: DrugShortageRecord[], previousSnapshot: Snapshot): DiffResult {
  const previousByHash = new Map(previousSnapshot.records.map((record) => [record.hash_id, record]));
  const previousByDrugKey = new Map<string, DrugShortageRecord[]>();

  for (const record of previousSnapshot.records) {
    const records = previousByDrugKey.get(record.drug_key) ?? [];
    records.push(record);
    previousByDrugKey.set(record.drug_key, records);
  }

  const result: DiffResult = {
    new_records: [],
    updated_records: [],
    unchanged_records: []
  };

  for (const record of currentRecords) {
    if (previousByHash.has(record.hash_id)) {
      result.unchanged_records.push(record);
      continue;
    }

    const previousSameDrug = previousByDrugKey.get(record.drug_key) ?? [];
    const hasChangedComparableFields = previousSameDrug.some(
      (previous) =>
        previous.shortage_status !== record.shortage_status ||
        previous.alternative_drug !== record.alternative_drug ||
        previous.record_hash !== record.record_hash
    );

    if (hasChangedComparableFields) {
      result.updated_records.push(record);
    } else {
      result.new_records.push(record);
    }
  }

  return result;
}
