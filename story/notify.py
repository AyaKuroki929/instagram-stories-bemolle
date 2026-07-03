"""LINE通知"""
from __future__ import annotations

from .config import LINE_TOKEN
from .util import line_broadcast


def notify(msg: str) -> None:
    # 通知失敗で本体を止めない（失敗ログは util.line_broadcast が stderr に残す）
    line_broadcast(msg, token=LINE_TOKEN)
