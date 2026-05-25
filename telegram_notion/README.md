# telegram_notion

Telegram 名片照片匯入 Notion 的 GitHub Actions 自動化。

## 流程

```text
Telegram 名片照片
  -> GitHub Actions 每 5 分鐘輪詢
  -> Gemini 2.5 Flash 擷取名片欄位
  -> Telegram 回覆確認按鈕
  -> Notion 藥品廠商名單新增資料
```

這個版本不需要常駐伺服器。GitHub Actions 不是即時 webhook，所以通常會有幾分鐘延遲。

## GitHub Secrets

到 repo 的 `Settings > Secrets and variables > Actions > Repository secrets` 新增：

| Secret | 說明 |
| --- | --- |
| `TELEGRAM_BOT_TOKEN` | BotFather 建立 Telegram bot 後取得 |
| `TELEGRAM_ALLOWED_USER_IDS` | 允許使用者的 Telegram user id；多人用逗號分隔 |
| `GEMINI_API_KEY` | Gemini API key |
| `NOTION_TOKEN` | Notion integration token |
| `NOTION_DATA_SOURCE_ID` | `23ae979d-cae8-803f-8666-000bad6c727d` |

也可以只新增一個 repository secret：`TELEGRAM_NOTION`，內容用多行 `.env` 格式：

```text
TELEGRAM_BOT_TOKEN=xxx
TELEGRAM_ALLOWED_USER_IDS=123456
GEMINI_API_KEY=xxx
NOTION_TOKEN=xxx
NOTION_DATA_SOURCE_ID=23ae979d-cae8-803f-8666-000bad6c727d
```

如果 workflow log 顯示 secret 是 missing，通常代表 secret 設在錯的 repo、整包 secret 內的 key 名稱沒有被解析到、或設在 Environment secrets 但 workflow 沒有綁定 environment。workflow 也會嘗試讀取同名 Repository variables，但請優先使用 Repository secrets。

workflow 會在 log 中顯示：

```text
Loaded from TELEGRAM_NOTION: ...
Ignored TELEGRAM_NOTION keys/lines: ...
```

只會顯示 key 名稱，不會印出 secret value。

可選：

| Secret / Env | 預設 | 說明 |
| --- | --- | --- |
| `GEMINI_MODEL` | `gemini-2.5-flash` | Gemini 模型 |
| `NOTION_VERSION` | `2026-03-11` | Notion API 版本 |
| `TELEGRAM_MAX_UPDATES` | `50` | 每輪最多處理 Telegram updates 數 |

## Notion 權限

Notion integration 必須被加入到「藥品廠商名單」資料庫的 connection/share 權限。

寫入欄位：

- `名稱`
- `公司`
- `職位`
- `電話`
- `電子郵件`
- `地址`
- `類別`
- `狀態`

新增時預設 `狀態 = 未通知`。無法判斷類別時，預設 `類別 = 其他友人`。

## 使用方式

1. 傳 `/start` 給 Telegram bot。
2. 到 GitHub Actions 手動執行一次 `Telegram Notion` workflow。
3. Bot 會回覆你的 Telegram user id。
4. 把這個 id 填進 `TELEGRAM_ALLOWED_USER_IDS`。
5. 傳名片照片給 bot。
6. 等下一輪 GitHub Actions 執行。
7. Bot 會回覆辨識結果，按 `確認新增`。
8. 下一輪 workflow 會把資料新增到 Notion。

如果偵測到疑似重複資料，Bot 會先問你要 `仍然新增` 或 `略過`。

## 重複偵測

腳本會查 Notion：

- email 完全相同
- 電話原文或末 6 碼相同
- `名稱 + 公司` 同時相同

命中疑似重複時不會直接新增。

## 本機檢查

```bash
TELEGRAM_BOT_TOKEN=dummy \
TELEGRAM_ALLOWED_USER_IDS=123456 \
GEMINI_API_KEY=dummy \
NOTION_TOKEN=dummy \
NOTION_DATA_SOURCE_ID=23ae979d-cae8-803f-8666-000bad6c727d \
python3 telegram_card_to_notion.py --validate-config
```

語法檢查：

```bash
python3 -m py_compile telegram_card_to_notion.py
```

## 注意事項

- 不要把 token 寫入 repo；請使用 GitHub Secrets。
- Telegram updates 最多保留約 24 小時，太久沒有執行 workflow 可能會漏訊息。
- workflow 檔案必須放在 repo 根目錄 `.github/workflows/telegram-notion.yml`，放在 `telegram_notion/.github/workflows/` 不會被 GitHub Actions 執行。
