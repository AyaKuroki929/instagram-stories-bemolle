#!/usr/bin/env python3
"""月次集客レポート用：Instagramフィード投稿数の月別集計（読み取り専用）

グルコン月次レポートの「インスタ投稿：○投稿」を自動で埋めるためのヘルパー。
/{IG_USER_ID}/media はフィード・リールを返す（ストーリーは含まれない）ので、
そのままフィード投稿数として月別に数える。

使い方（GitHub Actions report_helper.yml から実行）:
  python ig_report_helper.py 2026-06,2026-07
引数を省略すると「前月・当月」の2ヶ月を集計する。
"""
from __future__ import annotations

import os
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone

import requests

JST      = timezone(timedelta(hours=9))
META_API = "https://graph.facebook.com/v25.0"
TOKEN    = os.environ["META_ACCESS_TOKEN"]
IG_USER  = os.environ.get("IG_USER_ID", "17841470478859455")

TYPE_LABEL = {"IMAGE": "画像", "VIDEO": "動画/リール", "CAROUSEL_ALBUM": "カルーセル"}


def target_months(arg: str) -> list[str]:
    """引数（YYYY-MM,YYYY-MM…）を返す。省略時は前月＋当月。"""
    if arg:
        return [m.strip() for m in arg.split(",") if m.strip()]
    today = datetime.now(JST)
    first = today.replace(day=1)
    prev = (first - timedelta(days=1)).strftime("%Y-%m")
    return [prev, today.strftime("%Y-%m")]


def fetch_media(oldest_month: str) -> list[dict]:
    """media一覧を新しい順にページングで取得。対象最古月より前に達したら打ち切り。"""
    items: list[dict] = []
    url = f"{META_API}/{IG_USER}/media"
    params = {"fields": "id,timestamp,media_type", "limit": 100, "access_token": TOKEN}
    while url:
        r = requests.get(url, params=params, timeout=30)
        if not r.ok:
            raise Exception(f"media取得失敗: {r.status_code} {r.text[:300]}")
        data = r.json()
        page = data.get("data", [])
        items.extend(page)
        # 最古の対象月より前まで遡ったら終了（mediaは新しい順）
        if page:
            last_ts = page[-1].get("timestamp", "")
            try:
                last_month = (
                    datetime.fromisoformat(last_ts.replace("+0000", "+00:00"))
                    .astimezone(JST).strftime("%Y-%m")
                )
                if last_month < oldest_month:
                    break
            except Exception:
                pass
        url = data.get("paging", {}).get("next")
        params = {}  # nextにはクエリ込み
    return items


def main() -> None:
    months = target_months(sys.argv[1] if len(sys.argv) > 1 else "")
    media = fetch_media(min(months))

    print(f"取得メディア総数: {len(media)}件（新しい順・対象月まで遡及）\n")
    for month in months:
        counts: Counter = Counter()
        for m in media:
            ts = m.get("timestamp", "")
            try:
                dt = datetime.fromisoformat(ts.replace("+0000", "+00:00")).astimezone(JST)
            except Exception:
                continue
            if dt.strftime("%Y-%m") == month:
                counts[m.get("media_type", "OTHER")] += 1
        total = sum(counts.values())
        breakdown = "／".join(
            f"{TYPE_LABEL.get(k, k)}{v}" for k, v in counts.most_common()
        ) or "なし"
        print(f"[{month}] フィード投稿: {total}投稿（{breakdown}）")


if __name__ == "__main__":
    main()
