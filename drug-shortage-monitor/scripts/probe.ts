import "dotenv/config";

import axios from "axios";
import * as cheerio from "cheerio";
import crypto from "node:crypto";
import { chromium, type Page, type Request, type Response } from "playwright";

type CandidateEndpoint = {
  url: string;
  method: string;
  resourceType: string;
  status?: number;
  contentType?: string;
  sample?: unknown;
};

type DrugSupplyRecord = {
  sourcePage: string;
  rowHash: string;
  fields: Record<string, string>;
};

const BASE_URL = "https://dsms.fda.gov.tw/";
const TARGET_URLS = [
  "https://dsms.fda.gov.tw/LatestNews.aspx",
  "https://dsms.fda.gov.tw/DrugList.aspx?s=3"
];

const DEBUG = process.env.DEBUG_DSMS === "1";
const USER_AGENT =
  "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 " +
  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36";

function log(message: string, data?: unknown) {
  if (data === undefined) {
    console.log(`[probe] ${message}`);
    return;
  }
  console.log(`[probe] ${message}`, data);
}

function debug(message: string, data?: unknown) {
  if (!DEBUG) return;
  log(message, data);
}

function normalizeText(value: string) {
  return value.replace(/\s+/g, " ").trim();
}

function hashRecord(input: unknown) {
  return crypto.createHash("sha256").update(JSON.stringify(input)).digest("hex").slice(0, 16);
}

function isInterestingUrl(url: string) {
  if (!url.startsWith(BASE_URL)) return false;
  return /\.(css|js|png|jpe?g|gif|svg|ico|woff2?|ttf)(\?|$)/i.test(url) === false;
}

function looksLikeJson(contentType?: string, text?: string) {
  if (contentType?.toLowerCase().includes("application/json")) return true;
  const trimmed = text?.trim();
  return Boolean(trimmed && (trimmed.startsWith("{") || trimmed.startsWith("[")));
}

async function tryStaticFetch(url: string) {
  log(`Fetching static HTML: ${url}`);
  const response = await axios.get(url, {
    headers: { "User-Agent": USER_AGENT },
    timeout: 30_000,
    validateStatus: () => true
  });

  if (response.status >= 400) {
    throw new Error(`Static fetch failed for ${url}: HTTP ${response.status}`);
  }

  const html = String(response.data ?? "");
  debug(`Fetched ${html.length} chars from ${url}`);
  return html;
}

function extractTablesFromHtml(html: string, sourcePage: string): DrugSupplyRecord[] {
  const $ = cheerio.load(html);
  const records: DrugSupplyRecord[] = [];

  $("table").each((tableIndex, table) => {
    const headers = $(table)
      .find("tr")
      .first()
      .find("th,td")
      .map((_, cell) => normalizeText($(cell).text()))
      .get();

    $(table)
      .find("tr")
      .slice(1)
      .each((rowIndex, row) => {
        const values = $(row)
          .find("td")
          .map((_, cell) => normalizeText($(cell).text()))
          .get()
          .filter(Boolean);

        if (values.length === 0) return;

        const fields = Object.fromEntries(
          values.map((value, index) => [headers[index] || `column_${index + 1}`, value])
        );

        records.push({
          sourcePage,
          rowHash: hashRecord({ sourcePage, tableIndex, rowIndex, fields }),
          fields
        });
      });
  });

  return records;
}

async function captureNetworkCandidates(page: Page, url: string) {
  const requests = new Map<string, Request>();
  const candidates: CandidateEndpoint[] = [];

  page.on("request", (request) => {
    requests.set(request.url(), request);
    if (isInterestingUrl(request.url())) {
      debug("Request", {
        method: request.method(),
        resourceType: request.resourceType(),
        url: request.url()
      });
    }
  });

  page.on("response", async (response: Response) => {
    const request = requests.get(response.url());
    const contentType = response.headers()["content-type"];
    const resourceType = request?.resourceType() ?? "unknown";
    const method = request?.method() ?? "GET";

    if (!isInterestingUrl(response.url())) return;
    if (!["xhr", "fetch", "document"].includes(resourceType)) return;

    let bodyText = "";
    try {
      bodyText = await response.text();
    } catch (error) {
      debug(`Could not read response body for ${response.url()}: ${(error as Error).message}`);
    }

    if (!looksLikeJson(contentType, bodyText) && resourceType !== "xhr" && resourceType !== "fetch") {
      return;
    }

    let sample: unknown = bodyText.slice(0, 500);
    if (looksLikeJson(contentType, bodyText)) {
      try {
        sample = JSON.parse(bodyText);
      } catch {
        sample = bodyText.slice(0, 500);
      }
    }

    candidates.push({
      url: response.url(),
      method,
      resourceType,
      status: response.status(),
      contentType,
      sample
    });
  });

  log(`Opening with Playwright: ${url}`);
  await page.goto(url, { waitUntil: "networkidle", timeout: 60_000 });
  await page.waitForTimeout(2_000);

  return candidates;
}

async function extractVisibleTableData(page: Page, sourcePage: string): Promise<DrugSupplyRecord[]> {
  const html = await page.content();
  return extractTablesFromHtml(html, sourcePage);
}

async function main() {
  log("Starting DSMS probe");
  log("Targets", TARGET_URLS);

  const allStaticRecords: DrugSupplyRecord[] = [];
  for (const url of TARGET_URLS) {
    try {
      const html = await tryStaticFetch(url);
      const records = extractTablesFromHtml(html, url);
      log(`Static table rows found on ${url}: ${records.length}`);
      allStaticRecords.push(...records);
    } catch (error) {
      log(`Static fetch/parsing error: ${(error as Error).message}`);
    }
  }

  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ userAgent: USER_AGENT });
  const allCandidates: CandidateEndpoint[] = [];
  const allVisibleRecords: DrugSupplyRecord[] = [];

  try {
    for (const url of TARGET_URLS) {
      const candidates = await captureNetworkCandidates(page, url);
      allCandidates.push(...candidates);

      const visibleRecords = await extractVisibleTableData(page, url);
      log(`Visible Playwright table rows found on ${url}: ${visibleRecords.length}`);
      allVisibleRecords.push(...visibleRecords);
    }
  } finally {
    await browser.close();
  }

  const jsonCandidates = allCandidates.filter((candidate) =>
    String(candidate.contentType ?? "").toLowerCase().includes("json")
  );

  log(`Candidate XHR/fetch/document endpoints captured: ${allCandidates.length}`);
  if (jsonCandidates.length > 0) {
    log(`Potential JSON API endpoints found: ${jsonCandidates.length}`);
    console.dir(jsonCandidates.slice(0, 5), { depth: 4 });
  } else {
    log("No stable-looking JSON API endpoint found during this probe; using HTML table extraction fallback.");
  }

  const records = allVisibleRecords.length > 0 ? allVisibleRecords : allStaticRecords;
  log(`Total extracted records available: ${records.length}`);
  log("First 5 records:");
  console.dir(records.slice(0, 5), { depth: 6 });
}

main().catch((error) => {
  console.error("[probe] Fatal error");
  console.error(error);
  process.exitCode = 1;
});
