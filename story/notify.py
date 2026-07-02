"""LINE通知"""
from __future__ import annotations

import sys

import requests

from .config import LINE_TOKEN


def notify(msg: str) -> None:
    try:
        requests.post(
            "https://api.line.me/v2/bot/message/broadcast",
            headers={"Authorization": f"Bearer {LINE_TOKEN}"},
            json={"messages": [{"type": "text", "text": msg}]},
            timeout=10,
        )
    except Exception as e:
        # 通知失敗で本体を止めないのは従来どおり。ただし完全な無言はやめ、
        # Actionsログには残す（投稿失敗＋通知失敗の二重障害に気づけるように）
        print(f"LINE通知失敗（処理は継続）: {e}", file=sys.stderr)
