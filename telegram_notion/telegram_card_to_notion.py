#!/usr/bin/env python3
"""Import Telegram business card photos into a Notion data source.

This script is designed for GitHub Actions polling:
1. Fetch pending Telegram updates with getUpdates.
2. For photo/image messages, use Gemini 2.5 Flash to extract card fields.
3. Ask for confirmation in Telegram.
4. For confirmation callbacks, create a Notion page.

Required environment variables:
    TELEGRAM_BOT_TOKEN
    TELEGRAM_ALLOWED_USER_IDS
    GEMINI_API_KEY
    NOTION_TOKEN
    NOTION_DATA_SOURCE_ID

Optional environment variables:
    GEMINI_MODEL                  default: gemini-2.5-flash
    NOTION_VERSION                default: 2026-03-11
    TELEGRAM_MAX_UPDATES          default: 50
"""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import re
import sys
import textwrap
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any


ALLOWED_CATEGORIES = [
    "醫院單位",
    "公會協會",
    "其他友人",
    "藥劑科",
    "公家機關",
    "學校",
    "資訊公司",
    "藥品公司",
]
DEFAULT_STATUS = "未通知"
PAYLOAD_PREFIX = "CARDJSON:"
TELEGRAM_MESSAGE_LIMIT = 4096


class BotError(RuntimeError):
    """Raised for recoverable automation errors."""


@dataclass(frozen=True)
class Config:
    telegram_bot_token: str
    telegram_allowed_user_ids: set[int]
    gemini_api_key: str
    gemini_model: str
    notion_token: str
    notion_data_source_id: str
    notion_version: str
    max_updates: int

    @classmethod
    def from_env(cls) -> "Config":
        required = {
            "TELEGRAM_BOT_TOKEN": os.getenv("TELEGRAM_BOT_TOKEN", "").strip(),
            "GEMINI_API_KEY": os.getenv("GEMINI_API_KEY", "").strip(),
            "NOTION_TOKEN": os.getenv("NOTION_TOKEN", "").strip(),
            "NOTION_DATA_SOURCE_ID": os.getenv("NOTION_DATA_SOURCE_ID", "").strip(),
        }
        missing = [key for key, value in required.items() if not value]
        if missing:
            raise BotError(f"Missing required env vars: {', '.join(missing)}")

        allowed = parse_allowed_user_ids(os.getenv("TELEGRAM_ALLOWED_USER_IDS", ""))
        return cls(
            telegram_bot_token=required["TELEGRAM_BOT_TOKEN"],
            telegram_allowed_user_ids=allowed,
            gemini_api_key=required["GEMINI_API_KEY"],
            gemini_model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip(),
            notion_token=required["NOTION_TOKEN"],
            notion_data_source_id=required["NOTION_DATA_SOURCE_ID"],
            notion_version=os.getenv("NOTION_VERSION", "2026-03-11").strip(),
            max_updates=int(os.getenv("TELEGRAM_MAX_UPDATES", "50")),
        )


def parse_allowed_user_ids(raw: str) -> set[int]:
    ids: set[int] = set()
    for chunk in re.split(r"[,\s]+", raw.strip()):
        if not chunk:
            continue
        try:
            ids.add(int(chunk))
        except ValueError as exc:
            raise BotError(f"Invalid TELEGRAM_ALLOWED_USER_IDS value: {chunk}") from exc
    return ids


def request_json(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    payload: dict[str, Any] | None = None,
    timeout: int = 45,
) -> dict[str, Any]:
    body = None
    merged_headers = headers.copy() if headers else {}
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        merged_headers.setdefault("Content-Type", "application/json")

    req = urllib.request.Request(url, data=body, headers=merged_headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise BotError(f"HTTP {exc.code} for {url}: {error_body}") from exc
    except urllib.error.URLError as exc:
        raise BotError(f"Network error for {url}: {exc}") from exc

    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise BotError(f"Invalid JSON response from {url}: {raw[:500]}") from exc


def request_bytes(url: str, *, timeout: int = 45) -> bytes:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise BotError(f"HTTP {exc.code} for {url}: {error_body}") from exc
    except urllib.error.URLError as exc:
        raise BotError(f"Network error for {url}: {exc}") from exc


class TelegramClient:
    def __init__(self, token: str) -> None:
        self.token = token
        self.api_base = f"https://api.telegram.org/bot{token}"
        self.file_base = f"https://api.telegram.org/file/bot{token}"

    def call(self, method: str, payload: dict[str, Any] | None = None) -> Any:
        response = request_json(
            f"{self.api_base}/{method}",
            method="POST",
            payload=payload or {},
        )
        if not response.get("ok"):
            raise BotError(f"Telegram {method} failed: {response}")
        return response.get("result")

    def get_updates(self, limit: int) -> list[dict[str, Any]]:
        return self.call(
            "getUpdates",
            {
                "limit": limit,
                "timeout": 0,
                "allowed_updates": ["message", "callback_query"],
            },
        )

    def acknowledge_through(self, update_id: int) -> None:
        self.call("getUpdates", {"offset": update_id + 1, "limit": 1, "timeout": 0})

    def send_message(
        self,
        chat_id: int,
        text: str,
        reply_markup: dict[str, Any] | None = None,
    ) -> None:
        if len(text) > TELEGRAM_MESSAGE_LIMIT:
            text = text[: TELEGRAM_MESSAGE_LIMIT - 80] + "\n\n[訊息過長，已截短]"
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": True,
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup
        self.call("sendMessage", payload)

    def edit_message_reply_markup(
        self,
        chat_id: int,
        message_id: int,
        reply_markup: dict[str, Any] | None = None,
    ) -> None:
        self.call(
            "editMessageReplyMarkup",
            {"chat_id": chat_id, "message_id": message_id, "reply_markup": reply_markup},
        )

    def answer_callback_query(self, callback_query_id: str, text: str = "") -> None:
        payload: dict[str, Any] = {"callback_query_id": callback_query_id}
        if text:
            payload["text"] = text
        self.call("answerCallbackQuery", payload)

    def try_answer_callback_query(self, callback_query_id: str, text: str = "") -> None:
        try:
            self.answer_callback_query(callback_query_id, text)
        except BotError as exc:
            print(f"Warning: could not answer Telegram callback query: {exc}", file=sys.stderr)

    def try_edit_message_reply_markup(
        self,
        chat_id: int,
        message_id: int | None,
        reply_markup: dict[str, Any] | None = None,
    ) -> None:
        if not message_id:
            return
        try:
            self.edit_message_reply_markup(chat_id, message_id, reply_markup)
        except BotError as exc:
            print(f"Warning: could not edit Telegram reply markup: {exc}", file=sys.stderr)

    def get_file(self, file_id: str) -> dict[str, Any]:
        return self.call("getFile", {"file_id": file_id})

    def download_file(self, file_path: str) -> bytes:
        quoted_path = urllib.parse.quote(file_path, safe="/")
        return request_bytes(f"{self.file_base}/{quoted_path}")


class GeminiClient:
    def __init__(self, api_key: str, model: str) -> None:
        self.api_key = api_key
        self.model = model

    def extract_card(self, image_bytes: bytes, mime_type: str) -> dict[str, Any]:
        encoded = base64.b64encode(image_bytes).decode("ascii")
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
        response = request_json(
            url,
            method="POST",
            headers={"x-goog-api-key": self.api_key},
            payload={
                "contents": [
                    {
                        "parts": [
                            {"text": card_extraction_prompt()},
                            {
                                "inline_data": {
                                    "mime_type": mime_type,
                                    "data": encoded,
                                }
                            },
                        ]
                    }
                ],
                "generationConfig": {
                    "temperature": 0.1,
                    "responseMimeType": "application/json",
                    "responseJsonSchema": card_json_schema(),
                },
            },
            timeout=90,
        )
        text = extract_gemini_text(response)
        try:
            card = json.loads(text)
        except json.JSONDecodeError as exc:
            raise BotError(f"Gemini returned non-JSON text: {text[:500]}") from exc
        return normalize_card(card)


class NotionClient:
    def __init__(self, token: str, data_source_id: str, version: str) -> None:
        self.data_source_id = data_source_id
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Notion-Version": version,
            "Content-Type": "application/json",
        }

    def query_duplicates(self, card: dict[str, Any]) -> list[dict[str, Any]]:
        filters = duplicate_filters(card)
        if not filters:
            return []

        seen: set[str] = set()
        matches: list[dict[str, Any]] = []
        for filter_obj in filters:
            response = request_json(
                f"https://api.notion.com/v1/data_sources/{self.data_source_id}/query",
                method="POST",
                headers=self.headers,
                payload={"filter": filter_obj, "page_size": 5},
            )
            for page in response.get("results", []):
                page_id = page.get("id")
                if page_id and page_id not in seen:
                    seen.add(page_id)
                    matches.append(page)
        return matches

    def create_card_page(self, card: dict[str, Any], force: bool = False) -> str:
        response = request_json(
            "https://api.notion.com/v1/pages",
            method="POST",
            headers=self.headers,
            payload={
                "parent": {
                    "type": "data_source_id",
                    "data_source_id": self.data_source_id,
                },
                "properties": notion_properties(card),
                "children": notion_children(card, force=force),
            },
        )
        return response.get("url", "")


def card_extraction_prompt() -> str:
    categories = "、".join(ALLOWED_CATEGORIES)
    return textwrap.dedent(
        f"""
        請從這張名片照片擷取聯絡人資料，回傳符合 schema 的 JSON。

        規則：
        - 使用繁體中文。
        - 看起來不是名片時，is_business_card=false，其他欄位盡量留空。
        - name 是人名；company 是公司或單位名稱；title 是職稱。
        - phones 請保留原始可撥打格式，可以有多支。
        - email 請只放一個最主要的 email。
        - category 必須是其中之一：{categories}。
        - 如果是藥廠、藥商、醫療業務、醫藥公司，category 用「藥品公司」。
        - 無法判斷類別時，category 用「其他友人」。
        - confidence 介於 0 到 1，表示你對擷取結果的信心。
        - raw_text 放照片上可辨識出的主要文字，精簡即可。
        """
    ).strip()


def card_json_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "is_business_card": {"type": "boolean"},
            "name": {"type": "string"},
            "company": {"type": "string"},
            "title": {"type": "string"},
            "phones": {"type": "array", "items": {"type": "string"}},
            "email": {"type": "string"},
            "address": {"type": "string"},
            "category": {"type": "string", "enum": ALLOWED_CATEGORIES},
            "confidence": {"type": "number"},
            "raw_text": {"type": "string"},
        },
        "required": [
            "is_business_card",
            "name",
            "company",
            "title",
            "phones",
            "email",
            "address",
            "category",
            "confidence",
            "raw_text",
        ],
    }


def extract_gemini_text(response: dict[str, Any]) -> str:
    candidates = response.get("candidates") or []
    if not candidates:
        raise BotError(f"Gemini returned no candidates: {response}")
    parts = candidates[0].get("content", {}).get("parts", [])
    texts = [part.get("text", "") for part in parts if part.get("text")]
    text = "\n".join(texts).strip()
    if not text:
        raise BotError(f"Gemini returned no text: {response}")
    return text


def normalize_card(card: dict[str, Any]) -> dict[str, Any]:
    phones = card.get("phones") or []
    if isinstance(phones, str):
        phones = [phones]
    normalized = {
        "is_business_card": bool(card.get("is_business_card")),
        "name": clean_text(card.get("name", "")),
        "company": clean_text(card.get("company", "")),
        "title": clean_text(card.get("title", "")),
        "phones": [clean_text(phone) for phone in phones if clean_text(phone)],
        "email": clean_text(card.get("email", "")).lower(),
        "address": clean_text(card.get("address", "")),
        "category": clean_text(card.get("category", "")),
        "confidence": safe_float(card.get("confidence", 0)),
        "raw_text": clean_text(card.get("raw_text", "")),
    }
    if normalized["category"] not in ALLOWED_CATEGORIES:
        normalized["category"] = "其他友人"
    if not normalized["name"]:
        normalized["name"] = normalized["company"] or "未命名名片"
    return normalized


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def safe_float(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def duplicate_filters(card: dict[str, Any]) -> list[dict[str, Any]]:
    filters: list[dict[str, Any]] = []
    email = card.get("email")
    if email:
        filters.append({"property": "電子郵件", "email": {"equals": email}})

    for phone in card.get("phones", [])[:3]:
        digits = normalize_phone_digits(phone)
        filters.append({"property": "電話", "rich_text": {"contains": phone}})
        if len(digits) >= 6:
            filters.append({"property": "電話", "rich_text": {"contains": digits[-6:]}})

    name = card.get("name")
    company = card.get("company")
    if name and company:
        filters.append(
            {
                "and": [
                    {"property": "名稱", "title": {"contains": name}},
                    {"property": "公司", "rich_text": {"contains": company}},
                ]
            }
        )
    return filters


def normalize_phone_digits(phone: str) -> str:
    return re.sub(r"\D+", "", phone)


def notion_properties(card: dict[str, Any]) -> dict[str, Any]:
    phone_text = " / ".join(card.get("phones", []))
    props: dict[str, Any] = {
        "名稱": {"title": [{"text": {"content": card.get("name") or card.get("company") or "未命名名片"}}]},
        "公司": rich_text_property(card.get("company", "")),
        "職位": rich_text_property(card.get("title", "")),
        "電話": rich_text_property(phone_text),
        "地址": rich_text_property(card.get("address", "")),
        "類別": {"multi_select": [{"name": card.get("category") or "其他友人"}]},
        "狀態": {"status": {"name": DEFAULT_STATUS}},
    }
    email = card.get("email")
    if email:
        props["電子郵件"] = {"email": email}
    return props


def rich_text_property(value: str) -> dict[str, Any]:
    value = clean_text(value)
    return {"rich_text": [{"text": {"content": value}}]} if value else {"rich_text": []}


def notion_children(card: dict[str, Any], force: bool) -> list[dict[str, Any]]:
    lines = [
        f"來源：Telegram 名片自動辨識",
        f"AI：Gemini",
        f"信心：{card.get('confidence', 0):.2f}",
    ]
    if force:
        lines.append("重複偵測：使用者選擇仍然新增")
    raw_text = clean_text(card.get("raw_text", ""))
    if raw_text:
        lines.extend(["", "辨識文字：", raw_text[:1800]])
    return [
        {
            "object": "block",
            "type": "paragraph",
            "paragraph": {"rich_text": [{"type": "text", "text": {"content": "\n".join(lines)}}]},
        }
    ]


def encode_card_payload(card: dict[str, Any]) -> str:
    payload = {
        "name": card.get("name", ""),
        "company": card.get("company", ""),
        "title": card.get("title", ""),
        "phones": card.get("phones", []),
        "email": card.get("email", ""),
        "address": card.get("address", ""),
        "category": card.get("category", "其他友人"),
        "confidence": card.get("confidence", 0),
        "raw_text": clean_text(card.get("raw_text", ""))[:1200],
    }
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def decode_card_payload(message_text: str) -> dict[str, Any]:
    match = re.search(rf"{PAYLOAD_PREFIX}([A-Za-z0-9_\-=]+)", message_text or "")
    if not match:
        raise BotError("Cannot find card payload in Telegram confirmation message.")
    try:
        raw = base64.urlsafe_b64decode(match.group(1).encode("ascii"))
        return normalize_card(json.loads(raw.decode("utf-8")) | {"is_business_card": True})
    except (ValueError, json.JSONDecodeError) as exc:
        raise BotError("Cannot decode card payload from Telegram message.") from exc


def format_card_for_telegram(card: dict[str, Any], duplicates: list[dict[str, Any]] | None = None) -> str:
    lines = [
        "名片辨識結果",
        "",
        f"名稱：{card.get('name') or '-'}",
        f"公司：{card.get('company') or '-'}",
        f"職位：{card.get('title') or '-'}",
        f"電話：{' / '.join(card.get('phones', [])) or '-'}",
        f"電子郵件：{card.get('email') or '-'}",
        f"地址：{card.get('address') or '-'}",
        f"類別：{card.get('category') or '其他友人'}",
        f"信心：{card.get('confidence', 0):.2f}",
    ]
    if duplicates:
        lines.extend(["", "可能重複資料："])
        for page in duplicates[:3]:
            lines.append(f"- {summarize_notion_page(page)}")
        lines.append("")
        lines.append("請確認要仍然新增，或略過。")
    else:
        lines.extend(["", "請確認是否新增到 Notion。"])

    lines.extend(["", f"{PAYLOAD_PREFIX}{encode_card_payload(card)}"])
    return "\n".join(lines)


def summarize_notion_page(page: dict[str, Any]) -> str:
    props = page.get("properties", {})
    name = prop_text(props.get("名稱"))
    company = prop_text(props.get("公司"))
    email = prop_text(props.get("電子郵件"))
    phone = prop_text(props.get("電話"))
    chunks = [chunk for chunk in [name, company, email, phone] if chunk]
    url = page.get("url", "")
    summary = " / ".join(chunks) if chunks else page.get("id", "")
    return f"{summary} {url}".strip()


def prop_text(prop: dict[str, Any] | None) -> str:
    if not prop:
        return ""
    prop_type = prop.get("type")
    if prop_type in {"title", "rich_text"}:
        return "".join(item.get("plain_text", "") for item in prop.get(prop_type, []))
    if prop_type == "email":
        return prop.get("email") or ""
    if prop_type == "phone_number":
        return prop.get("phone_number") or ""
    if prop_type == "status":
        status = prop.get("status") or {}
        return status.get("name", "")
    if prop_type == "multi_select":
        return ", ".join(item.get("name", "") for item in prop.get("multi_select", []))
    return ""


def inline_keyboard(buttons: list[tuple[str, str]]) -> dict[str, Any]:
    return {"inline_keyboard": [[{"text": label, "callback_data": data} for label, data in buttons]]}


def is_authorized(config: Config, user_id: int | None) -> bool:
    return bool(user_id and user_id in config.telegram_allowed_user_ids)


def handle_message(
    config: Config,
    telegram: TelegramClient,
    gemini: GeminiClient,
    notion: NotionClient,
    message: dict[str, Any],
) -> None:
    chat_id = message["chat"]["id"]
    user_id = (message.get("from") or {}).get("id")
    text = clean_text(message.get("text", ""))

    if text.startswith("/start") or text.startswith("/whoami"):
        allowed_note = "已授權" if is_authorized(config, user_id) else "尚未授權"
        telegram.send_message(
            chat_id,
            f"你的 Telegram user id 是：{user_id}\n狀態：{allowed_note}\n\n請把這個 ID 放進 GitHub Secret TELEGRAM_ALLOWED_USER_IDS。",
        )
        return

    if not is_authorized(config, user_id):
        telegram.send_message(chat_id, "這個 Telegram 帳號尚未授權使用此 bot。請先設定 TELEGRAM_ALLOWED_USER_IDS。")
        return

    file_id, mime_type = extract_image_file(message)
    if not file_id:
        telegram.send_message(chat_id, "請傳一張名片照片，或把名片圖片作為檔案傳送。")
        return

    telegram.send_message(chat_id, "收到名片照片，正在用 Gemini 2.5 Flash 辨識...")
    file_info = telegram.get_file(file_id)
    image_bytes = telegram.download_file(file_info["file_path"])
    mime_type = mime_type or guess_mime_type(file_info["file_path"])

    card = gemini.extract_card(image_bytes, mime_type)
    if not card.get("is_business_card"):
        telegram.send_message(chat_id, "這張圖片看起來不像名片；請重新拍攝或傳更清楚的名片照片。")
        return

    duplicates = notion.query_duplicates(card)
    if duplicates:
        telegram.send_message(
            chat_id,
            format_card_for_telegram(card, duplicates),
            reply_markup=inline_keyboard([("仍然新增", "force_add"), ("略過", "cancel")]),
        )
    else:
        telegram.send_message(
            chat_id,
            format_card_for_telegram(card),
            reply_markup=inline_keyboard([("確認新增", "add"), ("取消", "cancel"), ("重新傳照片", "retry")]),
        )


def extract_image_file(message: dict[str, Any]) -> tuple[str | None, str | None]:
    photos = message.get("photo") or []
    if photos:
        best = max(photos, key=lambda item: item.get("file_size", 0))
        return best.get("file_id"), "image/jpeg"

    document = message.get("document") or {}
    mime_type = document.get("mime_type", "")
    if mime_type.startswith("image/"):
        return document.get("file_id"), mime_type
    return None, None


def guess_mime_type(path: str) -> str:
    guessed, _ = mimetypes.guess_type(path)
    return guessed or "image/jpeg"


def handle_callback(
    config: Config,
    telegram: TelegramClient,
    notion: NotionClient,
    callback: dict[str, Any],
) -> None:
    user_id = (callback.get("from") or {}).get("id")
    callback_id = callback["id"]
    message = callback.get("message") or {}
    chat_id = message.get("chat", {}).get("id")
    message_id = message.get("message_id")
    action = callback.get("data", "")

    if not chat_id:
        telegram.try_answer_callback_query(callback_id, "找不到原始訊息")
        return
    if not is_authorized(config, user_id):
        telegram.try_answer_callback_query(callback_id, "未授權")
        telegram.send_message(chat_id, "這個 Telegram 帳號尚未授權使用此 bot。")
        return

    if action == "cancel":
        telegram.try_answer_callback_query(callback_id, "已略過")
        telegram.try_edit_message_reply_markup(chat_id, message_id, None)
        telegram.send_message(chat_id, "已取消，沒有新增到 Notion。")
        return

    if action == "retry":
        telegram.try_answer_callback_query(callback_id, "請重新傳照片")
        telegram.send_message(chat_id, "請重新傳一張更清楚的名片照片，我會重新辨識。")
        return

    if action not in {"add", "force_add"}:
        telegram.try_answer_callback_query(callback_id, "未知操作")
        return

    card = decode_card_payload(message.get("text", ""))
    if action == "add":
        duplicates = notion.query_duplicates(card)
        if duplicates:
            telegram.try_answer_callback_query(callback_id, "發現可能重複")
            telegram.send_message(
                chat_id,
                format_card_for_telegram(card, duplicates),
                reply_markup=inline_keyboard([("仍然新增", "force_add"), ("略過", "cancel")]),
            )
            return

    page_url = notion.create_card_page(card, force=(action == "force_add"))
    telegram.try_answer_callback_query(callback_id, "已新增")
    telegram.try_edit_message_reply_markup(chat_id, message_id, None)
    telegram.send_message(chat_id, f"已新增到 Notion：\n{page_url or '(Notion 未回傳 URL)'}")


def process_update(
    config: Config,
    telegram: TelegramClient,
    gemini: GeminiClient,
    notion: NotionClient,
    update: dict[str, Any],
) -> None:
    if "message" in update:
        handle_message(config, telegram, gemini, notion, update["message"])
        return
    if "callback_query" in update:
        handle_callback(config, telegram, notion, update["callback_query"])
        return


def run_once(config: Config) -> int:
    telegram = TelegramClient(config.telegram_bot_token)
    gemini = GeminiClient(config.gemini_api_key, config.gemini_model)
    notion = NotionClient(config.notion_token, config.notion_data_source_id, config.notion_version)

    updates = telegram.get_updates(config.max_updates)
    if not updates:
        print("No Telegram updates.")
        return 0

    last_successful_update_id: int | None = None
    for update in sorted(updates, key=lambda item: item.get("update_id", 0)):
        update_id = update["update_id"]
        print(f"Processing update {update_id}...")
        try:
            process_update(config, telegram, gemini, notion, update)
        except Exception as exc:
            print(f"Failed to process update {update_id}: {exc}", file=sys.stderr)
            break
        last_successful_update_id = update_id

    if last_successful_update_id is not None:
        telegram.acknowledge_through(last_successful_update_id)
        print(f"Acknowledged Telegram updates through {last_successful_update_id}.")

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Poll Telegram business card photos into Notion.")
    parser.add_argument("--validate-config", action="store_true", help="Validate environment variables only.")
    args = parser.parse_args()

    started = time.time()
    config = Config.from_env()
    if args.validate_config:
        print("Configuration looks valid.")
        return 0

    result = run_once(config)
    print(f"Done in {time.time() - started:.1f}s.")
    return result


if __name__ == "__main__":
    raise SystemExit(main())
