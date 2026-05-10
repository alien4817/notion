import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";

import type { DrugShortageRecord, Snapshot } from "./types.js";

export const SNAPSHOT_PATH = path.resolve("data", "last_snapshot.json");

function isSnapshot(value: unknown): value is Snapshot {
  if (!value || typeof value !== "object") return false;
  const snapshot = value as Partial<Snapshot>;
  return typeof snapshot.updated_at === "string" && Array.isArray(snapshot.records);
}

export async function readSnapshot(snapshotPath = SNAPSHOT_PATH): Promise<Snapshot> {
  try {
    const fileContent = await readFile(snapshotPath, "utf8");
    const parsed = JSON.parse(fileContent) as unknown;

    if (!isSnapshot(parsed)) {
      throw new Error(`Invalid snapshot shape in ${snapshotPath}`);
    }

    return parsed;
  } catch (error) {
    const nodeError = error as NodeJS.ErrnoException;
    if (nodeError.code === "ENOENT") {
      console.warn(`[snapshot] Snapshot not found at ${snapshotPath}; starting with an empty snapshot.`);
      return { updated_at: "", records: [] };
    }

    throw error;
  }
}

export async function writeSnapshot(records: DrugShortageRecord[], snapshotPath = SNAPSHOT_PATH) {
  const snapshot: Snapshot = {
    updated_at: new Date().toISOString(),
    records
  };

  await mkdir(path.dirname(snapshotPath), { recursive: true });
  await writeFile(snapshotPath, `${JSON.stringify(snapshot, null, 2)}\n`, "utf8");

  return snapshot;
}
