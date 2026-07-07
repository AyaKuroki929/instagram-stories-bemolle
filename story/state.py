"""JSON状態ファイル管理

used_photos.json     … 使用済み写真の記録（クールダウン・類似回避・シリーズ回避の元データ）
recent_texts.json    … 最近の挨拶・締め文の履歴（締めの連日重複を防ぐ）
last_post.json       … サロン投稿の最終投稿日マーカー（二重投稿防止・第2の砦）
last_post_threads.json … Threads→ストーリーの最終投稿日マーカー

いずれも workflow が commit/push して永続化する。
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta

from .config import (
    COOLDOWN_DAYS,
    JST,
    LAST_POST_FILE,
    RECENT_TEXTS_FILE,
    USED_PHOTOS_FILE,
)


def load_used_photos() -> dict[str, dict]:
    """使用済み写真を読み込む（14日以上前は除外）"""
    if not os.path.exists(USED_PHOTOS_FILE):
        return {}
    try:
        with open(USED_PHOTOS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        cutoff = datetime.now(JST) - timedelta(days=COOLDOWN_DAYS)
        result = {}
        for fid, info in data.items():
            if isinstance(info, str):  # 旧フォーマット互換
                info = {"ts": info, "hash": ""}
            if datetime.fromisoformat(info.get("ts", "1970-01-01T00:00:00+00:00")) > cutoff:
                result[fid] = info
        return result
    except Exception:
        return {}


def get_recent_hashes(days: int) -> list[str]:
    """直近N日以内に使った写真のハッシュ一覧を返す（類似チェック用）"""
    if not os.path.exists(USED_PHOTOS_FILE):
        return []
    try:
        with open(USED_PHOTOS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        cutoff = datetime.now(JST) - timedelta(days=days)
        return [
            info["hash"]
            for info in data.values()
            if isinstance(info, dict)
            and datetime.fromisoformat(info.get("ts", "1970-01-01T00:00:00+00:00")) > cutoff
            and info.get("hash")
        ]
    except Exception:
        return []


def get_recent_series(days: int) -> set[str]:
    """直近N日以内に使った写真の撮影シリーズ接頭辞の集合を返す。"""
    if not os.path.exists(USED_PHOTOS_FILE):
        return set()
    try:
        with open(USED_PHOTOS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        cutoff = datetime.now(JST) - timedelta(days=days)
        return {
            info["series"]
            for info in data.values()
            if isinstance(info, dict)
            and datetime.fromisoformat(info.get("ts", "1970-01-01T00:00:00+00:00")) > cutoff
            and info.get("series")
        }
    except Exception:
        return set()


def save_used_photo(file_id: str, photo_hash: str = "", series: str = "") -> None:
    """使用した写真IDとハッシュ・撮影シリーズを used_photos.json に記録する"""
    used = load_used_photos()
    used[file_id] = {"ts": datetime.now(JST).isoformat(), "hash": photo_hash, "series": series}
    try:
        # アトミック書き込み（途中killでの破損＝クールダウン全解除を防ぐ）
        tmp = USED_PHOTOS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(used, f, ensure_ascii=False, indent=2)
        os.replace(tmp, USED_PHOTOS_FILE)
    except Exception as e:
        print(f"used_photos.json 保存失敗: {e}", file=sys.stderr)


def load_recent_closings(n: int = 10) -> list[str]:
    """直近に使った締めの一言を返す（締めの連日重複を避けるためプロンプトに渡す）。
    挨拶は「おはようございます。」固定にしたため、変化をつけるのは締め。"""
    try:
        if os.path.exists(RECENT_TEXTS_FILE):
            with open(RECENT_TEXTS_FILE, encoding="utf-8") as f:
                data = json.load(f)
            return [e.get("closing", "") for e in data[-n:] if e.get("closing")]
    except Exception:
        pass
    return []


def save_recent_text(greeting: str, closing: str = "") -> None:
    """使った挨拶・締め文を履歴に追記（直近30件保持・workflowがcommitして永続化）。"""
    try:
        data = []
        if os.path.exists(RECENT_TEXTS_FILE):
            with open(RECENT_TEXTS_FILE, encoding="utf-8") as f:
                data = json.load(f)
        data.append({"date": datetime.now(JST).date().isoformat(),
                     "greeting": greeting, "closing": closing})
        tmp = RECENT_TEXTS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data[-30:], f, ensure_ascii=False, indent=2)
        os.replace(tmp, RECENT_TEXTS_FILE)
    except Exception as e:
        print(f"recent_texts.json保存失敗: {e}", file=sys.stderr)


# ── 二重投稿防止（idempotency）────────────────────────────────────
def posted_today_local(path: str = LAST_POST_FILE) -> bool:
    """リポジトリの最終投稿日マーカーで今日(JST)投稿済みか判定。
    Meta /stories APIが既存投稿を返さない不具合（実際に二重投稿が発生）への第2の砦。"""
    try:
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                d = json.load(f)
            return d.get("date") == datetime.now(JST).date().isoformat()
    except Exception as e:
        print(f"{path}読込失敗（無視）: {e}", file=sys.stderr)
    return False


def mark_posted_local(path: str = LAST_POST_FILE) -> None:
    """投稿成功時に最終投稿日マーカーを更新（workflowがcommit/pushして永続化）。"""
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(
                {"date": datetime.now(JST).date().isoformat(),
                 "ts": datetime.now(JST).isoformat()},
                f, ensure_ascii=False, indent=2,
            )
        os.replace(tmp, path)
    except Exception as e:
        print(f"last_post.json保存失敗: {e}", file=sys.stderr)
