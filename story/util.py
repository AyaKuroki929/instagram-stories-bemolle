"""共通ユーティリティ（storyパッケージ内・gbp_post.py からも利用する共通基盤）

このモジュールは import 時に環境変数を要求しない。
（story/config.py は post_story 用の必須envをimport時に検証するが、
 gbp_post.py など必要なenvが異なるスクリプトからも安全に import できるよう、
 ここではトークン類を「呼び出し時」に解決する）
"""
from __future__ import annotations

import json
import os
import sys

import anthropic
import requests


# ── JSON状態ファイル ──────────────────────────────────────────
def load_json(path: str, default):
    """JSONファイルを読む。無い・壊れている場合は default を返す。"""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path: str, data) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ── Claude応答の取り扱い ──────────────────────────────────────
def extract_json(text: str) -> dict:
    """括弧の深さを追跡して最初のJSONオブジェクトを正確に抽出する"""
    start = text.find("{")
    if start == -1:
        raise ValueError("JSONが見つかりません")
    depth = 0
    in_string = False
    escape = False
    for i, ch in enumerate(text[start:], start):
        if escape:
            escape = False
            continue
        if ch == "\\" and in_string:
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start:i + 1])
    raise ValueError("JSONの終端が見つかりません")


def message_text(message) -> str:
    """Claude応答からテキスト部分を安全に取り出す。
    message.content[0].text は content が空だと IndexError で
    原因の分かりにくい失敗になるため、テキストブロックを結合して返す。"""
    text = "".join(b.text for b in message.content if getattr(b, "type", "") == "text")
    if not text:
        raise ValueError(f"Claude応答にテキストがありません（stop_reason={message.stop_reason}）")
    return text


def claude_text(*, model: str, max_tokens: int, messages: list, api_key: str = "", **kwargs) -> str:
    """Claudeを呼び出してテキスト応答を返す（system / temperature 等は kwargs で渡す）。"""
    client = anthropic.Anthropic(api_key=api_key or os.environ["ANTHROPIC_API_KEY"])
    msg = client.messages.create(model=model, max_tokens=max_tokens, messages=messages, **kwargs)
    return message_text(msg)


# ── LINE配信（Claude通知Bot broadcast）────────────────────────
def line_broadcast(messages, token: str = "", *, raise_on_error: bool = False, timeout: int = 15) -> bool:
    """LINE broadcast を送る。
    messages: 文字列（1通のテキスト）または LINE メッセージオブジェクトのリスト。
    raise_on_error=False（既定）: 失敗しても例外を投げず、ログだけ残して False を返す。
    raise_on_error=True: 失敗時に例外（配信自体が主目的の処理用）。"""
    if isinstance(messages, str):
        messages = [{"type": "text", "text": messages}]
    token = token or os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
    try:
        r = requests.post(
            "https://api.line.me/v2/bot/message/broadcast",
            headers={"Authorization": f"Bearer {token}"},
            json={"messages": messages},
            timeout=timeout,
        )
        if not r.ok:
            raise Exception(f"LINE broadcast {r.status_code}: {r.text[:200]}")
        return True
    except Exception as e:
        if raise_on_error:
            raise
        print(f"LINE通知失敗（処理は継続）: {e}", file=sys.stderr)
        return False
