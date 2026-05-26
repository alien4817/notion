#!/usr/bin/env python3
"""Batch runner for Telegram business card imports.

The base importer contains all card extraction and Notion behavior. This runner
only changes polling semantics so one bad update does not stop later photos.
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
            "This card update failed. I skipped it and continued with the other photos.\n"
            f"update_id: {update_id}\n"
            f"Error: {exc}",
        )
    except Exception as notify_exc:
        print(
            f"Warning: could not notify Telegram failure for update {update_id}: {notify_exc}",
            file=sys.stderr,
        )


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
            importer.process_update(config, telegram, gemini, notion, update)
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
