#!/usr/bin/env python3
"""Batch runner for Telegram business card imports.

The base importer contains the card extraction helpers. This runner changes the
polling behavior so multiple Telegram updates are handled independently, and
new cards are written to Notion immediately after recognition.
"""

from __future__ import annotations

import argparse
import sys
import time
from typing import Any

import telegram_card_to_notion as importer


def chat_id_for_update(update: dict[str, Any]) -> int | None:
    if "message" in update:
        return (update["message"].get("chat") or {}).get("id")
    if "callback_query" in update:
        message = update["callback_query"].get("message") or {}
        return (message.get("chat") or {}).get("id")
    return None


def notify_update_failure(
    telegram: importer.TelegramClient,
    update: dict[str, Any],
    update_id: int,
    exc: Exception,
) -> None:
    chat_id = chat_id_for_update(update)
    if not chat_id:
        return

    try:
        telegram.send_message(
            chat_id,
            "這筆名片處理失敗，已先跳過並繼續處理其他照片。\n"
            f"update_id: {update_id}\n"
            f"錯誤：{exc}",
        )
    except Exception as notify_exc:
        print(
            f"Warning: could not notify Telegram failure for update {update_id}: {notify_exc}",
            file=sys.stderr,
        )


def handle_message_and_create(
    config: importer.Config,
    telegram: importer.TelegramClient,
    gemini: importer.GeminiClient,
    notion: importer.NotionClient,
    message: dict[str, Any],
) -> None:
    chat_id = message["chat"]["id"]
    user_id = (message.get("from") or {}).get("id")
    text = importer.clean_text(message.get("text", ""))

    if text.startswith("/start") or text.startswith("/whoami"):
        allowed_note = "已授權" if importer.is_authorized(config, user_id) else "尚未授權"
        telegram.send_message(
            chat_id,
            f"你的 Telegram user id 是：{user_id}\n狀態：{allowed_note}\n\n請把這個 ID 放進 GitHub Secret TELEGRAM_ALLOWED_USER_IDS。",
        )
        return

    if not importer.is_authorized(config, user_id):
        telegram.send_message(chat_id, "這個 Telegram 帳號尚未授權使用此 bot。請先設定 TELEGRAM_ALLOWED_USER_IDS。")
        return

    file_id, mime_type = importer.extract_image_file(message)
    if not file_id:
        telegram.send_message(chat_id, "請傳一張名片照片，或把名片圖片作為檔案傳送。")
        return

    telegram.send_message(chat_id, "收到名片照片，正在辨識並準備寫入 Notion...")
    file_info = telegram.get_file(file_id)
    image_bytes = telegram.download_file(file_info["file_path"])
    mime_type = mime_type or importer.guess_mime_type(file_info["file_path"])

    card = gemini.extract_card(image_bytes, mime_type)
    if not card.get("is_business_card"):
        telegram.send_message(chat_id, "這張圖片看起來不像名片；請重新拍攝或傳更清楚的名片照片。")
        return

    duplicates = notion.query_duplicates(card)
    print(
        "Card extracted for direct import: "
        f"name={card.get('name') or '-'}, company={card.get('company') or '-'}, duplicates={len(duplicates)}"
    )
    if duplicates:
        telegram.send_message(
            chat_id,
            importer.format_card_for_telegram(card, duplicates),
            reply_markup=importer.inline_keyboard([("仍然新增", "force_add"), ("略過", "cancel")]),
        )
        print("Notion create skipped because duplicate confirmation is required.")
        return

    page_url = notion.create_card_page(card)
    print(f"Notion page created: {page_url or '(no URL returned)'}")
    telegram.send_message(
        chat_id,
        "已辨識並新增到 Notion：\n"
        f"{page_url or '(Notion 未回傳 URL)'}",
    )


def process_update(
    config: importer.Config,
    telegram: importer.TelegramClient,
    gemini: importer.GeminiClient,
    notion: importer.NotionClient,
    update: dict[str, Any],
) -> None:
    if "message" in update:
        handle_message_and_create(config, telegram, gemini, notion, update["message"])
        return
    if "callback_query" in update:
        importer.handle_callback(config, telegram, notion, update["callback_query"])
        return


def run_once(config: importer.Config) -> int:
    telegram = importer.TelegramClient(config.telegram_bot_token)
    gemini = importer.GeminiClient(config.gemini_api_key, config.gemini_model)
    notion = importer.NotionClient(
        config.notion_token,
        config.notion_data_source_id,
        config.notion_version,
    )

    updates = telegram.get_updates(config.max_updates)
    if not updates:
        print("No Telegram updates.")
        return 0

    last_seen_update_id: int | None = None
    successful_count = 0
    failed_count = 0

    for update in sorted(updates, key=lambda item: item.get("update_id", 0)):
        update_id = update["update_id"]
        last_seen_update_id = update_id
        print(f"Processing update {update_id}...")
        try:
            process_update(config, telegram, gemini, notion, update)
        except Exception as exc:
            failed_count += 1
            print(f"Failed to process update {update_id}: {exc}", file=sys.stderr)
            notify_update_failure(telegram, update, update_id, exc)
            continue
        successful_count += 1

    if last_seen_update_id is not None:
        telegram.acknowledge_through(last_seen_update_id)
        print(f"Acknowledged Telegram updates through {last_seen_update_id}.")

    print(
        "Processed Telegram updates: "
        f"success={successful_count}, failed={failed_count}, total={len(updates)}."
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Batch poll Telegram business card photos into Notion.")
    parser.add_argument("--validate-config", action="store_true", help="Validate environment variables only.")
    args = parser.parse_args()

    started = time.time()
    config = importer.Config.from_env()
    if args.validate_config:
        print("Configuration looks valid.")
        return 0

    result = run_once(config)
    print(f"Done in {time.time() - started:.1f}s.")
    return result


if __name__ == "__main__":
    raise SystemExit(main())
