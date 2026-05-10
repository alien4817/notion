export type RawFDARecord = {
  source_url: string;
  detail_url?: string;
  detail_text?: string;
  fields: Record<string, string>;
};

export type DrugShortageRecord = {
  drug_name: string;
  ingredient: string;
  license_no: string;
  shortage_status: string;
  alternative_drug: string;
  updated_at: string;
  source_url: string;
  detail_url: string;
  detail_text: string;
  drug_key: string;
  record_hash: string;
  hash_id: string;
  checked_at: string;
};

export type Snapshot = {
  updated_at: string;
  records: DrugShortageRecord[];
};

export type DiffResult = {
  new_records: DrugShortageRecord[];
  updated_records: DrugShortageRecord[];
  unchanged_records: DrugShortageRecord[];
};
