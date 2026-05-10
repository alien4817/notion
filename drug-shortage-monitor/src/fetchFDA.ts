import axios from "axios";
import * as cheerio from "cheerio";
import { chromium, type Page } from "playwright";

import type { RawFDARecord } from "./types.js";

const DEFAULT_TARGET_URLS = [
  "https://dsms.fda.gov.tw/LatestNews.aspx",
  "https://dsms.fda.gov.tw/DrugList.aspx?s=3"
];

const USER_AGENT =
  "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 " +
  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36";

type ExtractedRawFDARecord = RawFDARecord & {
  detail_link_text?: string;
};

function getTargetUrls() {
  const configuredUrls = process.env.FDA_DSMS_URL?.split(/[,\n]/)
    .map((url) => url.trim())
    .filter(Boolean);

  return configuredUrls && configuredUrls.length > 0 ? configuredUrls : DEFAULT_TARGET_URLS;
}

function log(message: string, data?: unknown) {
  if (data === undefined) {
    console.log(`[fetchFDA] ${message}`);
    return;
  }

  console.log(`[fetchFDA] ${message}`, data);
}

function cleanText(value: string) {
  return value.replace(/\s+/g, " ").trim();
}

async function fetchHtml(url: string) {
  const response = await axios.get(url, {
    headers: { "User-Agent": USER_AGENT },
    timeout: 30_000,
    validateStatus: () => true
  });

  if (response.status >= 400) {
    throw new Error(`HTTP ${response.status} while fetching ${url}`);
  }

  return String(response.data ?? "");
}

function extractTablesFromHtml(html: string, sourceUrl: string): ExtractedRawFDARecord[] {
  const $ = cheerio.load(html);
  const records: ExtractedRawFDARecord[] = [];

  $("table").each((_, table) => {
    const headers = $(table)
      .find("tr")
      .first()
      .find("th,td")
      .map((_, cell) => cleanText($(cell).text()))
      .get();

    const isTargetTable = headers.includes("公告日期") || headers.includes("更新日期");
    if (!isTargetTable) return;

    $(table)
      .find("tr")
      .slice(1)
      .each((_, row) => {
        const values = $(row)
          .find("td")
          .map((_, cell) => cleanText($(cell).text()))
          .get()
          .filter(Boolean);

        if (values.length === 0) return;
        if (values.length < Math.min(headers.length, 3)) return;

        const detailLinkText = cleanText($(row).find("a").first().text());
        records.push({
          source_url: sourceUrl,
          detail_link_text: detailLinkText || undefined,
          fields: Object.fromEntries(
            values.map((value, index) => [headers[index] || `column_${index + 1}`, value])
          )
        });
      });
  });

  return records;
}

function extractUsefulBodyText(bodyText: string) {
  const text = bodyText.replace(/\r/g, "").replace(/\n{3,}/g, "\n\n").trim();
  const withoutHeader = text.replace(/^[\s\S]*?操作手冊\s*/u, "").trim();
  return withoutHeader.replace(/\s*返回首頁[\s\S]*$/u, "").trim();
}

async function extractDetailFromCurrentPage(page: Page) {
  const detailUrl = page.url();
  const bodyText = await page.locator("body").innerText({ timeout: 10_000 });
  const html = await page.content();
  const $ = cheerio.load(html);
  const detailFields: Record<string, string> = {};

  $("table tr").each((_, row) => {
    const cells = $(row)
      .find("th,td")
      .map((_, cell) => cleanText($(cell).text()))
      .get()
      .filter(Boolean);

    if (cells.length === 2) {
      detailFields[cells[0]] = cells[1];
      return;
    }

    if (cells.length === 3 && cells[0] !== "原因分析") {
      detailFields["原因分析"] = cells[0];
      detailFields["評估結果說明"] = cells[1];
      detailFields["評估更新日期"] = cells[2];
    }
  });

  return {
    detail_url: detailUrl,
    detail_text: extractUsefulBodyText(bodyText),
    detail_fields: detailFields
  };
}

async function enrichRecordsWithDetails(page: Page, sourceUrl: string, records: ExtractedRawFDARecord[]) {
  const enriched: RawFDARecord[] = [];

  for (const record of records) {
    if (!record.detail_link_text) {
      enriched.push(record);
      continue;
    }

    try {
      const link = page.locator("a").filter({ hasText: record.detail_link_text }).first();
      await link.click({ timeout: 15_000 });
      await page.waitForLoadState("networkidle", { timeout: 60_000 }).catch(() => undefined);
      await page.waitForTimeout(500);

      const detail = await extractDetailFromCurrentPage(page);
      enriched.push({
        ...record,
        fields: {
          ...record.fields,
          ...detail.detail_fields
        },
        detail_url: detail.detail_url,
        detail_text: detail.detail_text
      });

      await page.goto(sourceUrl, { waitUntil: "networkidle", timeout: 60_000 });
      await page.waitForTimeout(300);
    } catch (error) {
      log(`Detail fetch failed for "${record.detail_link_text}": ${(error as Error).message}`);
      enriched.push(record);
      await page.goto(sourceUrl, { waitUntil: "networkidle", timeout: 60_000 }).catch(() => undefined);
    }
  }

  return enriched;
}

async function fetchWithStaticHtml(url: string) {
  log(`Fetching static HTML: ${url}`);
  const html = await fetchHtml(url);
  const records = extractTablesFromHtml(html, url);
  log(`Static table rows found: ${records.length}`);
  return records;
}

async function fetchWithPlaywright(url: string) {
  log(`Opening with Playwright detail scraper: ${url}`);
  const browser = await chromium.launch({ headless: true });

  try {
    const page = await browser.newPage({ userAgent: USER_AGENT });
    await page.goto(url, { waitUntil: "networkidle", timeout: 60_000 });
    await page.waitForTimeout(1_000);

    const html = await page.content();
    const records = extractTablesFromHtml(html, url);
    log(`Playwright table rows found: ${records.length}`);
    return await enrichRecordsWithDetails(page, url, records);
  } finally {
    await browser.close();
  }
}

export async function fetchFDARecords() {
  const allRecords: RawFDARecord[] = [];

  for (const url of getTargetUrls()) {
    try {
      const records = await fetchWithStaticHtml(url);

      if (records.length > 0) {
        allRecords.push(...(await fetchWithPlaywright(url)));
        continue;
      }

      log(`No rows found from static HTML; trying Playwright fallback for ${url}`);
      allRecords.push(...(await fetchWithPlaywright(url)));
    } catch (error) {
      log(`Static fetch failed for ${url}: ${(error as Error).message}`);
      allRecords.push(...(await fetchWithPlaywright(url)));
    }
  }

  return allRecords;
}
