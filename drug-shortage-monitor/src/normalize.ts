import { createDrugKey, createRecordHash } from "./hash.js";
import type { DrugShortageRecord, RawFDARecord } from "./types.js";

function cleanText(value: string | undefined) {
  return (value ?? "").replace(/\s+/g, " ").trim();
}

function firstField(fields: Record<string, string>, names: string[]) {
  for (const name of names) {
    const value = cleanText(fields[name]);
    if (value) return value;
  }

  return "";
}

function inferStatus(raw: RawFDARecord) {
  if (raw.source_url.includes("DrugList.aspx?s=3")) return "短缺";

  const explicitStatus = firstField(raw.fields, [
    "短缺狀態",
    "供應狀態",
    "狀態",
    "辦理狀態",
    "處理情形"
  ]);

  if (explicitStatus) return explicitStatus;

  const subject = firstField(raw.fields, ["主旨", "公告內容", "品項", "column_3"]);
  if (subject.includes("短缺")) return "短缺";
  if (subject.includes("徵求")) return "公開徵求";

  return "";
}

function inferDrugName(raw: RawFDARecord) {
  const explicitName = firstField(raw.fields, [
    "藥品名稱",
    "品名",
    "中文品名",
    "英文品名",
    "藥品品項",
    "品項",
    "column_2"
  ]);

  if (explicitName && explicitName !== "西藥") return explicitName;

  const subject = firstField(raw.fields, ["主旨", "公告內容", "column_3"]);
  const quoted = subject.match(/[「『](.+?)[」』]/);
  return quoted?.[1] ? cleanText(quoted[1]) : subject;
}

export function normalizeFDARecords(rawRecords: RawFDARecord[], checkedAt = new Date().toISOString()) {
  return rawRecords.map((raw): DrugShortageRecord => {
    const normalizedWithoutHash = {
      drug_name: inferDrugName(raw),
      ingredient: firstField(raw.fields, ["成分", "主成分", "成分名稱", "學名 (成分)", "學名", "ingredient"]),
      license_no: firstField(raw.fields, ["許可證字號", "許可證", "license_no"]),
      shortage_status: inferStatus(raw),
      alternative_drug: firstField(raw.fields, [
        "替代藥品",
        "替代品項",
        "替代藥",
        "替代方案",
        "建議替代藥品",
        "評估結果說明"
      ]),
      updated_at: firstField(raw.fields, ["更新日期", "公告日期", "異動日期", "通報日期", "updated_at"]),
      source_url: raw.source_url,
      detail_url: raw.detail_url ?? "",
      detail_text: raw.detail_text ?? "",
      checked_at: checkedAt
    };

    const drugKey = createDrugKey(normalizedWithoutHash);
    const recordHash = createRecordHash(normalizedWithoutHash);

    return {
      ...normalizedWithoutHash,
      drug_key: drugKey,
      record_hash: recordHash,
      hash_id: recordHash
    };
  });
}
