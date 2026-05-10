import "dotenv/config";

import { diffRecords } from "./diff.js";
import { fetchFDARecords } from "./fetchFDA.js";
import { normalizeFDARecords } from "./normalize.js";
import { writeNotionChanges } from "./notion.js";
import { readSnapshot, writeSnapshot } from "./snapshot.js";

async function main() {
  console.log("[monitor] Starting drug shortage monitor");

  const rawRecords = await fetchFDARecords();
  const records = normalizeFDARecords(rawRecords);
  const snapshot = await readSnapshot();
  const diff = diffRecords(records, snapshot);

  console.log(`[monitor] Normalized record count: ${records.length}`);
  console.log(`[monitor] new_records: ${diff.new_records.length}`);
  console.log(`[monitor] updated_records: ${diff.updated_records.length}`);
  console.log(`[monitor] unchanged_records: ${diff.unchanged_records.length}`);
  console.log("[monitor] First 5 records:");
  console.dir(records.slice(0, 5), { depth: 6 });

  await writeNotionChanges(diff.new_records, diff.updated_records);

  await writeSnapshot(records);
  console.log("[monitor] Snapshot updated: data/last_snapshot.json");
}

main().catch((error) => {
  console.error("[monitor] Fatal error");
  console.error(error);
  process.exitCode = 1;
});
