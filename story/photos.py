"""写真選択

ahash（見た目の指紋）・顔検出・撮影シリーズ判定と、
Google Drive からの背景写真取得、Drive障害時の予備写真キャッシュ。
"""
from __future__ import annotations

import os
import random
import re
import sys
from io import BytesIO

import requests
from PIL import Image

from .config import (
    FALLBACK_DIR,
    FALLBACK_MAX,
    GDRIVE_CLIENT,
    GDRIVE_FOLDER,
    GDRIVE_FOLDER_COMMON,
    GDRIVE_FOLDER_FACIAL,
    GDRIVE_FOLDER_SLIM,
    GDRIVE_REFRESH,
    GDRIVE_SECRET,
    SERIES_DAYS,
    SIMILARITY_DAYS,
    SIMILARITY_THRESHOLD,
)
from .state import get_recent_hashes, get_recent_series, load_used_photos, save_used_photo


def photo_ahash(img_bytes: bytes) -> str:
    """平均ハッシュ（ahash）で画像の見た目フィンガープリントを返す。PIL のみで計算。"""
    try:
        img = Image.open(BytesIO(img_bytes)).convert("L").resize((8, 8), Image.LANCZOS)
        pixels = list(img.getdata())
        avg = sum(pixels) / len(pixels)
        bits = "".join("1" if p >= avg else "0" for p in pixels)
        return format(int(bits, 2), "016x")
    except Exception:
        return ""


_FACE_CASCADE = None


def detect_faces(pil_img: "Image.Image") -> list | None:
    """画像中の顔の矩形 [(x,y,w,h),...] を返す。
    OpenCV未導入・読み込み失敗時は None（＝判定不能）。検出成功で0件なら []。"""
    global _FACE_CASCADE
    try:
        import cv2
        import numpy as np
        if _FACE_CASCADE is None:
            _FACE_CASCADE = cv2.CascadeClassifier(
                cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
            )
        gray = cv2.cvtColor(np.array(pil_img.convert("RGB")), cv2.COLOR_RGB2GRAY)
        faces = _FACE_CASCADE.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(28, 28))
        return [tuple(int(v) for v in f) for f in faces]
    except Exception as e:
        print(f"顔検出スキップ（判定不能）: {e}", file=sys.stderr)
        return None


def hash_distance(h1: str, h2: str) -> int:
    if not h1 or not h2:
        return 64
    return bin(int(h1, 16) ^ int(h2, 16)).count("1")


def series_key(name: str) -> str:
    """同一撮影シリーズの接頭辞を返す（末尾の連番を除いた部分）。
    例: mur_051.JPG→'mur_'、139A9685.JPG→'139A'、20250112-9.jpg→'20250112-'。
    ahash/dhashでは捉えられない『同じ撮影の似た構図』を、ファイル名のシリーズで判定するため。"""
    base = os.path.splitext(name)[0]
    m = re.match(r"^(.*?)(\d+)$", base)
    return (m.group(1) if m else base).strip().lower()


def save_fallback_photo(img_bytes: bytes) -> None:
    """Drive取得成功時に実写真を最大FALLBACK_MAX枚までリポジトリに貯める（Drive障害時の背景用）。
    障害日にランダム表示するための多様性を確保するため、既存予備と似すぎる写真は追加しない。
    規定数に達したら打ち止め（gitの肥大化防止）。ファイル名にahashを使い重複も自動回避。"""
    try:
        os.makedirs(FALLBACK_DIR, exist_ok=True)
        existing = [f for f in os.listdir(FALLBACK_DIR) if f.endswith(".jpg")]
        if len(existing) >= FALLBACK_MAX:
            return  # 既に規定数＝打ち止め
        new_hash = photo_ahash(img_bytes)
        if not new_hash:
            return
        # 多様性のため、既存予備とahashが近い（似ている）なら追加しない
        for fn in existing:
            if hash_distance(new_hash, os.path.splitext(fn)[0]) <= SIMILARITY_THRESHOLD:
                return
        with open(os.path.join(FALLBACK_DIR, f"{new_hash}.jpg"), "wb") as f:
            f.write(img_bytes)
        print(f"予備写真を追加（{len(existing) + 1}/{FALLBACK_MAX}枚目）")
    except Exception as e:
        print(f"予備写真保存失敗: {e}", file=sys.stderr)


def load_fallback_photo() -> bytes | None:
    """貯めた予備写真からランダムに1枚返す（Drive取得不可時にグラデを避ける）"""
    try:
        if os.path.isdir(FALLBACK_DIR):
            files = [f for f in os.listdir(FALLBACK_DIR) if f.endswith(".jpg")]
            if files:
                pick = random.choice(files)
                with open(os.path.join(FALLBACK_DIR, pick), "rb") as f:
                    data = f.read()
                if data:
                    print(f"Drive取得不可 → 予備写真{len(files)}枚からランダム選択: {pick}")
                    return data
    except Exception as e:
        print(f"予備写真読込失敗: {e}", file=sys.stderr)
    return None


# ── Google Drive から背景写真を取得（コース内容に連動） ─────────
def get_drive_photo(course_pool: list[str]) -> bytes | None:
    if not GDRIVE_REFRESH:
        return None
    try:
        r = requests.post("https://oauth2.googleapis.com/token", data={
            "grant_type": "refresh_token",
            "refresh_token": GDRIVE_REFRESH,
            "client_id": GDRIVE_CLIENT,
            "client_secret": GDRIVE_SECRET,
        }, timeout=15)
        r.raise_for_status()
        token = r.json()["access_token"]

        # 今日のコースに対応するフォルダを決める（共通は常にフォールバック）
        has_slim   = any("痩身" in c for c in course_pool)
        has_facial = any("肌質" in c for c in course_pool)
        if has_slim and has_facial:
            folder_ids = [GDRIVE_FOLDER_SLIM, GDRIVE_FOLDER_FACIAL, GDRIVE_FOLDER_COMMON, GDRIVE_FOLDER]
        elif has_slim:
            folder_ids = [GDRIVE_FOLDER_SLIM, GDRIVE_FOLDER_COMMON, GDRIVE_FOLDER]
        elif has_facial:
            folder_ids = [GDRIVE_FOLDER_FACIAL, GDRIVE_FOLDER_COMMON, GDRIVE_FOLDER]
        else:
            folder_ids = [GDRIVE_FOLDER_COMMON, GDRIVE_FOLDER]

        # 優先フォルダから順に写真を探す
        auth_headers = {"Authorization": f"Bearer {token}"}
        used = load_used_photos()
        recent_h = get_recent_hashes(SIMILARITY_DAYS)
        recent_series = get_recent_series(SERIES_DAYS)

        for folder_id in folder_ids:
            r2 = requests.get(
                "https://www.googleapis.com/drive/v3/files",
                headers=auth_headers,
                params={
                    "q": f"'{folder_id}' in parents and mimeType contains 'image/' and trashed=false",
                    "fields": "files(id,name,thumbnailLink)",
                },
                timeout=15,
            )
            r2.raise_for_status()
            files = r2.json().get("files", [])
            if not files:
                continue

            # 14日クールダウン除外。全使用済みならフォルダ全体から選ぶ
            fresh = [f for f in files if f["id"] not in used]
            candidates = fresh if fresh else files
            reset_label = "" if fresh else "（全使用済みのためリセット）"

            # 同じ撮影シリーズ（mur_等）が連日続かないよう、直近で使ったシリーズを除外。
            # ahash/dhashでは『同じ機器クローズアップ』の知覚的類似を捉えられないため、シリーズで多様性を担保。
            if recent_series:
                diverse = [c for c in candidates if series_key(c["name"]) not in recent_series]
                if diverse:
                    candidates = diverse
                else:
                    print(f"  {folder_id[:8]}は直近シリーズ以外なし→シリーズ制限を一時解除", file=sys.stderr)

            # 類似回避は「直近と最も似ていない候補」を選ぶ（max-min距離方式）。
            # 旧方式の「しきい値を満たす最初の1枚」だと、しきい値ぎりぎり（距離9 vs 閾値8）の
            # 酷似写真がすり抜けて数日連続で似た写真が出ていたため、全候補を採点して最大化する。
            if recent_h and len(candidates) > 1:
                scored = []
                for c in candidates:
                    score = 64  # サムネ取得失敗＝未知。十分に異なる扱い
                    thumb_url = c.get("thumbnailLink")
                    if thumb_url:
                        try:
                            tr = requests.get(thumb_url, timeout=10)
                            if tr.status_code == 200:
                                h = photo_ahash(tr.content)
                                if h:
                                    score = min(hash_distance(h, rh) for rh in recent_h)
                        except Exception:
                            pass  # サムネ取得失敗は無視（score=64のまま）
                    scored.append((score, c))
                best = max(s for s, _ in scored)
                chosen = random.choice([c for s, c in scored if s == best])  # 同点はランダム
                if best <= SIMILARITY_THRESHOLD:
                    print(f"⚠️ 全候補が直近と類似（最大min距離={best}≤{SIMILARITY_THRESHOLD}）。"
                          f"最も差がある写真を選択（要：素材追加）")
                else:
                    print(f"類似回避OK: 直近との最小ahash距離={best}の写真を選択")
            else:
                chosen = random.choice(candidates)

            r3 = requests.get(
                f"https://www.googleapis.com/drive/v3/files/{chosen['id']}",
                headers=auth_headers,
                params={"alt": "media"},
                timeout=30,
            )
            r3.raise_for_status()
            h_full = photo_ahash(r3.content)
            save_used_photo(chosen["id"], h_full, series_key(chosen["name"]))
            save_fallback_photo(r3.content)  # 予備写真キャッシュ（Drive障害時の背景）
            print(f"Drive写真: {chosen['name']}（シリーズ:{series_key(chosen['name'])}）{reset_label}")
            return r3.content

        return None
    except Exception as e:
        print(f"Drive取得失敗（グラデーション背景で代替）: {e}", file=sys.stderr)
        return None
