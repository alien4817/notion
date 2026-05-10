import axios from "axios";

import type { DrugShortageRecord } from "./types.js";

const NOTION_VERSION = "2022-06-28";
const DETAIL_HEADING = "重點內容";
const LEGACY_DETAIL_HEADING = "子頁面擷取內容";

type NotionWriteSummary = {
  attempted: number;
  succeeded: number;
  failed: number;
  skipped: number;
};

type NotionAppendSummary = NotionWriteSummary & {
  skipped: number;
};

type NotionDatabaseSchema = {
  titlePropertyName: string;
};

function toRichText(value: string) {
  return value ? [{ text: { content: value } }] : [];
}

function chunkText(value: string, chunkSize = 1900) {
  const chunks: string[] = [];
  for (let index = 0; index < value.length; index += chunkSize) {
    chunks.push(value.slice(index, index + chunkSize));
  }
  return chunks;
}

function toNotionDate(value: string) {
  if (!value) return null;

  const normalizedDate = value.match(/^\d{4}\/\d{2}\/\d{2}$/) ? value.replaceAll("/", "-") : value;
  const isDateOnly = /^\d{4}-\d{2}-\d{2}$/.test(normalizedDate);
  const isIsoDateTime = /^\d{4}-\d{2}-\d{2}T/.test(normalizedDate);

  if (!isDateOnly && !isIsoDateTime) return null;

  const timestamp = Date.parse(normalizedDate);
  if (Number.isNaN(timestamp)) return null;

  return { start: normalizedDate };
}

function buildProperties(record: DrugShortageRecord, isNewCase: boolean, schema: NotionDatabaseSchema) {
  return {
    [schema.titlePropertyName]: {
      title: toRichText(record.drug_name)
    },
    "成分名稱": {
      rich_text: toRichText(record.ingredient)
    },
    "許可證字號": {
      rich_text: toRichText(record.license_no)
    },
    "短缺狀態": {
      select: record.shortage_status ? { name: record.shortage_status } : null
    },
    "替代藥品": {
      rich_text: toRichText(record.alternative_drug)
    },
    "更新日期": {
      date: toNotionDate(record.updated_at)
    },
    "來源網址": {
      url: record.detail_url || record.source_url || null
    },
    "Hash ID": {
      rich_text: toRichText(record.hash_id)
    },
    "最後檢查時間": {
      date: toNotionDate(record.checked_at)
    },
    "是否新案件": {
      checkbox: isNewCase
    },
    "備註": {
      rich_text: []
    }
  };
}

function buildChildren(record: DrugShortageRecord) {
  if (!record.detail_text && !record.detail_url) return [];

  const blocks: unknown[] = [
    {
      object: "block",
      type: "heading_2",
      heading_2: {
        rich_text: toRichText(DETAIL_HEADING)
      }
    }
  ];

  if (record.detail_url) {
    blocks.push({
      object: "block",
      type: "paragraph",
      paragraph: {
        rich_text: [{ text: { content: `子頁面網址：${record.detail_url}`, link: { url: record.detail_url } } }]
      }
    });
  }

  for (const chunk of chunkText(record.detail_text).slice(0, 90)) {
    blocks.push({
      object: "block",
      type: "paragraph",
      paragraph: {
        rich_text: toRichText(chunk)
      }
    });
  }

  return blocks;
}

async function queryPageByTitle(record: DrugShortageRecord, schema: NotionDatabaseSchema) {
  const token = process.env.NOTION_TOKEN;
  const databaseId = process.env.NOTION_DATABASE_ID;

  if (!token || !databaseId) {
    throw new Error("Missing NOTION_TOKEN or NOTION_DATABASE_ID in .env");
  }

  const response = await axios.post(
    `https://api.notion.com/v1/databases/${databaseId}/query`,
    {
      filter: {
        property: schema.titlePropertyName,
        title: {
          equals: record.drug_name
        }
      },
      page_size: 1
    },
    {
      headers: {
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
        "Notion-Version": NOTION_VERSION
      },
      timeout: 30_000
    }
  );

  return response.data.results?.[0]?.id as string | undefined;
}

async function queryPageByHashId(record: DrugShortageRecord) {
  const token = process.env.NOTION_TOKEN;
  const databaseId = process.env.NOTION_DATABASE_ID;

  if (!token || !databaseId) {
    throw new Error("Missing NOTION_TOKEN or NOTION_DATABASE_ID in .env");
  }

  const response = await axios.post(
    `https://api.notion.com/v1/databases/${databaseId}/query`,
    {
      filter: {
        property: "Hash ID",
        rich_text: {
          equals: record.hash_id
        }
      },
      page_size: 1
    },
    {
      headers: {
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
        "Notion-Version": NOTION_VERSION
      },
      timeout: 30_000
    }
  );

  return response.data.results?.[0]?.id as string | undefined;
}

async function pageAlreadyHasDetailChildren(pageId: string) {
  const token = process.env.NOTION_TOKEN;

  if (!token) {
    throw new Error("Missing NOTION_TOKEN in .env");
  }

  const response = await axios.get(`https://api.notion.com/v1/blocks/${pageId}/children?page_size=50`, {
    headers: {
      Authorization: `Bearer ${token}`,
      "Notion-Version": NOTION_VERSION
    },
    timeout: 30_000
  });

  const children = response.data.results as Array<{
    type: string;
    heading_2?: { rich_text?: Array<{ plain_text?: string }> };
  }>;

  return children.some(
    (block) =>
      block.type === "heading_2" &&
      block.heading_2?.rich_text?.some(
        (text) => text.plain_text === DETAIL_HEADING || text.plain_text === LEGACY_DETAIL_HEADING
      )
  );
}

async function appendChildBlocks(pageId: string, record: DrugShortageRecord) {
  const token = process.env.NOTION_TOKEN;

  if (!token) {
    throw new Error("Missing NOTION_TOKEN in .env");
  }

  const children = buildChildren(record);
  if (children.length === 0) return false;

  await axios.patch(
    `https://api.notion.com/v1/blocks/${pageId}/children`,
    { children },
    {
      headers: {
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
        "Notion-Version": NOTION_VERSION
      },
      timeout: 30_000
    }
  );

  return true;
}

async function getNotionDatabaseSchema(): Promise<NotionDatabaseSchema> {
  const token = process.env.NOTION_TOKEN;
  const databaseId = process.env.NOTION_DATABASE_ID;

  if (!token || !databaseId) {
    throw new Error("Missing NOTION_TOKEN or NOTION_DATABASE_ID in .env");
  }

  const response = await axios.get(`https://api.notion.com/v1/databases/${databaseId}`, {
    headers: {
      Authorization: `Bearer ${token}`,
      "Notion-Version": NOTION_VERSION
    },
    timeout: 30_000
  });

  const properties = response.data.properties as Record<string, { type: string }>;
  const titlePropertyName = Object.entries(properties).find(([, property]) => property.type === "title")?.[0];

  if (!titlePropertyName) {
    throw new Error("The configured Notion database does not have a title property.");
  }

  return { titlePropertyName };
}

async function createNotionPage(record: DrugShortageRecord, isNewCase: boolean, schema: NotionDatabaseSchema) {
  const token = process.env.NOTION_TOKEN;
  const databaseId = process.env.NOTION_DATABASE_ID;

  if (!token || !databaseId) {
    throw new Error("Missing NOTION_TOKEN or NOTION_DATABASE_ID in .env");
  }

  await axios.post(
    "https://api.notion.com/v1/pages",
    {
      parent: { database_id: databaseId },
      properties: buildProperties(record, isNewCase, schema),
      children: buildChildren(record)
    },
    {
      headers: {
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
        "Notion-Version": NOTION_VERSION
      },
      timeout: 30_000
    }
  );
}

export async function writeNotionChanges(newRecords: DrugShortageRecord[], updatedRecords: DrugShortageRecord[]) {
  const summary: NotionWriteSummary = {
    attempted: newRecords.length + updatedRecords.length,
    succeeded: 0,
    failed: 0,
    skipped: 0
  };

  if (summary.attempted === 0) {
    console.log("[notion] No new or updated records to write.");
    return summary;
  }

  if (!process.env.NOTION_TOKEN || !process.env.NOTION_DATABASE_ID) {
    console.warn("[notion] Missing NOTION_TOKEN or NOTION_DATABASE_ID in .env; skipped Notion write.");
    return summary;
  }

  let schema: NotionDatabaseSchema;
  try {
    schema = await getNotionDatabaseSchema();
  } catch (error) {
    summary.failed = summary.attempted;
    console.error("[notion] Failed to read database schema", error);
    return summary;
  }

  const writes = [
    ...newRecords.map((record) => ({ record, isNewCase: true })),
    ...updatedRecords.map((record) => ({ record, isNewCase: false }))
  ];

  for (const item of writes) {
    try {
      const existingPageId = await queryPageByHashId(item.record);
      if (existingPageId) {
        summary.skipped += 1;
        continue;
      }

      await createNotionPage(item.record, item.isNewCase, schema);
      summary.succeeded += 1;
    } catch (error) {
      summary.failed += 1;
      const axiosError = error as { response?: { status?: number; data?: unknown }; message?: string };
      console.error("[notion] Failed to write record", {
        drug_name: item.record.drug_name,
        hash_id: item.record.hash_id,
        status: axiosError.response?.status,
        error: axiosError.response?.data ?? axiosError.message ?? error
      });
    }
  }

  console.log(
    `[notion] Write summary: attempted=${summary.attempted}, succeeded=${summary.succeeded}, failed=${summary.failed}, skipped=${summary.skipped}`
  );

  return summary;
}

export async function appendNotionChildBlocksForRecords(records: DrugShortageRecord[]) {
  const summary: NotionAppendSummary = {
    attempted: records.length,
    succeeded: 0,
    failed: 0,
    skipped: 0
  };

  if (records.length === 0) return summary;

  if (!process.env.NOTION_TOKEN || !process.env.NOTION_DATABASE_ID) {
    console.warn("[notion] Missing NOTION_TOKEN or NOTION_DATABASE_ID in .env; skipped child block append.");
    summary.skipped = records.length;
    return summary;
  }

  let schema: NotionDatabaseSchema;
  try {
    schema = await getNotionDatabaseSchema();
  } catch (error) {
    summary.failed = summary.attempted;
    console.error("[notion] Failed to read database schema", error);
    return summary;
  }

  for (const record of records) {
    try {
      if (!record.detail_text) {
        summary.skipped += 1;
        continue;
      }

      const pageId = await queryPageByTitle(record, schema);
      if (!pageId) {
        summary.skipped += 1;
        console.warn("[notion] No existing page found for child blocks", { drug_name: record.drug_name });
        continue;
      }

      if (await pageAlreadyHasDetailChildren(pageId)) {
        summary.skipped += 1;
        continue;
      }

      const appended = await appendChildBlocks(pageId, record);
      if (appended) {
        summary.succeeded += 1;
      } else {
        summary.skipped += 1;
      }
    } catch (error) {
      summary.failed += 1;
      const axiosError = error as { response?: { status?: number; data?: unknown }; message?: string };
      console.error("[notion] Failed to append child blocks", {
        drug_name: record.drug_name,
        status: axiosError.response?.status,
        error: axiosError.response?.data ?? axiosError.message ?? error
      });
    }
  }

  console.log(
    `[notion] Child block append summary: attempted=${summary.attempted}, succeeded=${summary.succeeded}, failed=${summary.failed}, skipped=${summary.skipped}`
  );

  return summary;
}
