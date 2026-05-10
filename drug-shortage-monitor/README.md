# drug-shortage-monitor

第一階段工具：分析台灣食藥署西藥供應資訊平台的資料載入方式，並在找不到穩定 JSON API 時，用 Playwright 擷取頁面表格資料。

目標頁面：

- http://dsms.fda.gov.tw/LatestNews.aspx
- http://dsms.fda.gov.tw/DrugList.aspx?s=3

## 安裝

```bash
npm install
```

`postinstall` 會安裝 Playwright Chromium。若瀏覽器尚未安裝，也可以手動執行：

```bash
npx playwright install chromium
```

## 執行 probe

```bash
npm run probe
```

執行後會：

1. 先用 `axios` 抓取靜態 HTML，嘗試解析表格。
2. 用 Playwright 開啟頁面並監看 `document`、`xhr`、`fetch` 請求。
3. 優先列出疑似 JSON API endpoint。
4. 若沒有穩定 JSON API，輸出 Playwright 從頁面表格擷取到的資料。
5. 在終端機輸出前 5 筆資料。

## 執行 monitor

```bash
npm run monitor
```

`monitor` 會從食藥署頁面取得原始表格資料，整理成標準格式，輸出資料筆數與前 5 筆資料。

標準格式：

```ts
{
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
}
```

`hash_id` 由以下欄位串接後產生 SHA-256：

```text
drug_name + ingredient + license_no + shortage_status + alternative_drug + updated_at
```

## Notion 寫入設定

`monitor` 會把 `new_records` 與 `updated_records` 新增到 Notion database。`updated_records` 也會新增成一筆新紀錄，不會覆蓋舊資料，方便保留歷史紀錄。

程式會用 Playwright 點擊 FDA 列表中的子頁面，擷取公告內文或藥品詳情中的 `原因分析`、`評估結果說明`、`更新日期` 等內容。寫入 Notion 時，這些子頁內容會放在該筆 Notion page 的 child blocks，標題為 `重點內容`。

在 `.env` 加入：

```bash
NOTION_TOKEN=secret_xxx
NOTION_DATABASE_ID=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

檔名需要是 `.env`。如果目前資料夾裡是 `env`，請改名或另外建立 `.env`：

```bash
cp .env.example .env
```

Notion database 需要建立以下欄位：

| Notion 欄位 | 類型 |
| --- | --- |
| 藥品名稱或既有標題欄位 | Title |
| 成分名稱 | Text / rich_text |
| 許可證字號 | Text / rich_text |
| 短缺狀態 | Select |
| 替代藥品 | Text / rich_text |
| 更新日期 | Date |
| 來源網址 | URL |
| Hash ID | Text / rich_text |
| 最後檢查時間 | Date |
| 是否新案件 | Checkbox |
| 備註 | Text / rich_text |

Notion database 只能有一個 Title 欄位；如果你的 Title 欄位叫 `標題`，程式會自動偵測並使用它，不需要另外建立 `藥品名稱` Title 欄位。

Notion integration 也需要被加入到該 database 的連線或分享權限中，否則 API 會回傳權限錯誤。

如果沒有設定 `NOTION_TOKEN` 或 `NOTION_DATABASE_ID`，程式會略過 Notion 寫入，只保留終端機警告。若 Notion API 寫入失敗，程式會輸出錯誤內容，但仍會繼續更新本機 snapshot。

## GitHub Actions

已提供 workflow：

```text
.github/workflows/drug-shortage-monitor.yml
```

排程每天執行兩次：

| 台灣時間 | UTC cron |
| --- | --- |
| 08:00 | `0 0 * * *` |
| 16:00 | `0 8 * * *` |

workflow 也支援 `workflow_dispatch`，可以在 GitHub Actions 頁面手動執行。

請到 GitHub repository：

```text
Settings > Secrets and variables > Actions > Repository secrets
```

新增以下 Secrets：

| Secret | 說明 |
| --- | --- |
| `NOTION_TOKEN` | Notion integration token |
| `NOTION_DATABASE_ID` | 要寫入的 Notion database ID |
| `FDA_DSMS_URL` | FDA 目標網址。可填多行或用逗號分隔多個網址 |

`FDA_DSMS_URL` 建議值：

```text
http://dsms.fda.gov.tw/LatestNews.aspx
http://dsms.fda.gov.tw/DrugList.aspx?s=3
```

程式一定會把內建預設的兩個 FDA 頁面納入監控；若有設定 `FDA_DSMS_URL`，會再合併額外網址並去重。
若已在 GitHub Secret 填入 `https://dsms.fda.gov.tw/...`，程式會自動再嘗試對應的 `http://` URL，避免 DSMS HTTPS/TLS 連線被 GitHub Actions runner 關閉時直接失敗。
若 DSMS 同時對 HTTP/HTTPS 都回傳 `socket hang up` 或 `ERR_EMPTY_RESPONSE`，monitor 會視為上游網站暫時無法從該 runner 存取。此時會使用既有 snapshot 補寫 Notion，並用 `Hash ID` 查重避免重複新增；如果 snapshot 也是空的，才會略過 Notion 寫入。

## 除錯

建立 `.env`：

```bash
cp .env.example .env
```

開啟詳細 log：

```bash
DEBUG_DSMS=1 npm run probe
```

請不要把 token 或密鑰寫死在程式裡，使用 `.env` 管理。
