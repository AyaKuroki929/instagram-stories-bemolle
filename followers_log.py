#!/usr/bin/env python3
"""Instagramフォロワー数の日次記録（読み取り→followers_log.jsonに追記）

グルコン月次レポートの「インスタフォロワー：その月に何名増えたか」を出すための土台。
毎日23:50 JST（GitHub Actions followers_log.yml）に実行し、
{"YYYY-MM-DD": フォロワー数} を followers_log.json に積む。
増加数 = 当月末の値 - 前月末の値（月中は 最新値 - 前月末の値）。

GitHub Actionsのcron遅延で日付をまたいでも正しい日に記録できるよう、
実行時刻が昼前（12時未満）なら「前日の終値」として前日の日付で記録する。
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

JST      = timezone(timedelta(hours=9))
META_API = "https://graph.facebook.com/v25.0"
TOKEN    = os.environ["META_ACCESS_TOKEN"]
IG_USER  = os.environ.get("IG_USER_ID", "17841470478859455")
LOG_PATH = Path(__file__).parent / "followers_log.json"


def main() -> None:
    r = requests.get(
        f"{META_API}/{IG_USER}",
        params={"fields": "followers_count", "access_token": TOKEN},
        timeout=30,
    )
    r.raise_for_status()
    count = int(r.json()["followers_count"])

    now = datetime.now(JST)
    day = now if now.hour >= 12 else now - timedelta(days=1)
    key = day.strftime("%Y-%m-%d")

    log = json.loads(LOG_PATH.read_text()) if LOG_PATH.exists() else {}
    log[key] = count
    LOG_PATH.write_text(
        json.dumps(dict(sorted(log.items())), ensure_ascii=False, indent=2) + "\n"
    )
    print(f"記録: {key} = {count}名（実行 {now:%Y-%m-%d %H:%M} JST）")


if __name__ == "__main__":
    main()
