"""投稿

画像アップロード（imgbb / Vercel Blob）と Instagram Stories への投稿、
Meta API による既投稿判定。
"""
from __future__ import annotations

import base64
import random
import sys
import time
from datetime import datetime

import requests

from .config import BLOB_TOKEN, IG_USER_ID, IMGBB_KEY, JST, META_API, META_TOKEN


# ── IG User ID ────────────────────────────────────────────────
def get_ig_user_id() -> str:
    return IG_USER_ID


# ── imgbb にアップロード ──────────────────────────────────────
def upload_to_imgbb(image_bytes: bytes) -> str:
    b64 = base64.b64encode(image_bytes).decode()
    r = requests.post("https://api.imgbb.com/1/upload", data={
        "key": IMGBB_KEY,
        "image": b64,
        "expiration": 7200,
    }, timeout=30)
    r.raise_for_status()
    # HTTP 200 でも success=false や欠損レスポンスがあり得るため、URLの存在を確認する
    data = r.json()
    url = data.get("data", {}).get("url") if isinstance(data.get("data"), dict) else None
    if not url:
        raise Exception(f"imgbb応答異常（URLなし）: {str(data)[:250]}")
    return url


def upload_to_blob(image_bytes: bytes) -> str:
    """Vercel Blob にアップロードして公開URLを返す（imgbb↔Meta取得不調の保険）。"""
    pathname = f"story-{int(time.time())}-{random.randint(1000, 9999)}.jpg"
    r = requests.put(
        "https://vercel.com/api/blob/",
        params={"pathname": pathname},
        headers={
            "authorization": f"Bearer {BLOB_TOKEN}",
            "x-api-version": "12",
            "x-content-type": "image/jpeg",
            "x-add-random-suffix": "1",
            "x-vercel-blob-access": "public",
        },
        data=image_bytes,
        timeout=30,
    )
    if not r.ok:
        raise Exception(f"Blob {r.status_code}: {r.text[:250]}")
    url = r.json().get("url")
    if not url:
        raise Exception(f"Blob応答異常（URLなし）: {r.text[:250]}")
    return url


def upload_image(image_bytes: bytes, host: str) -> str:
    return upload_to_blob(image_bytes) if host == "blob" else upload_to_imgbb(image_bytes)


# ── Instagram Stories に投稿 ──────────────────────────────────
def post_to_stories(ig_user_id: str, image_bytes: bytes) -> str:
    # 投稿が「止まらない」ための多重防御。Metaのメディア取得が一時的に失敗
    # （code 9004 / subcode 2207052）し、imgbb↔Metaの取得不調がしばらく続くと
    # 同一ホストの再アップだけでは復旧しない（2回実害）。
    # そこで試行ごとに画像ホストを切替（Blob優先・imgbb保険）＋新URLで作り直す。最大3回。
    hosts = ["blob", "imgbb", "blob"] if BLOB_TOKEN else ["imgbb", "imgbb", "imgbb"]
    creation_id = None
    last_err = ""
    for attempt, host in enumerate(hosts):
        try:
            image_url = upload_image(image_bytes, host)
            print(f"画像URL（試行{attempt + 1}/3・{host}）: {image_url}")
        except Exception as e:
            last_err = f"{host}アップロード失敗: {e}"
            print(f"  {last_err}（試行{attempt + 1}/3）", file=sys.stderr)
            if attempt < 2:
                time.sleep(8)
            continue
        r = requests.post(f"{META_API}/{ig_user_id}/media", data={
            "image_url": image_url,
            "media_type": "STORIES",
            "access_token": META_TOKEN,
        }, timeout=30)
        if r.ok:
            creation_id = r.json()["id"]
            break
        last_err = f"{r.status_code} {r.text[:400]}"
        print(f"  コンテナ作成失敗（試行{attempt + 1}/3）: {last_err}", file=sys.stderr)
        if attempt < 2:
            time.sleep(10)
    if creation_id is None:
        raise Exception(last_err)

    # Instagramのコンテナ処理完了を待つ（最大60秒）
    status = ""
    for attempt in range(12):
        time.sleep(5)
        status_r = requests.get(f"{META_API}/{creation_id}", params={
            "fields": "status_code",
            "access_token": META_TOKEN,
        }, timeout=15)
        if status_r.ok:
            status = status_r.json().get("status_code", "")
            print(f"  コンテナ状態: {status} (試行{attempt+1})")
            if status == "FINISHED":
                break
            if status == "ERROR":
                raise Exception("コンテナ処理エラー（Instagram側）")
        # IN_PROGRESS or unknown → 待機継続
    if status != "FINISHED":
        # 60秒待っても完了しない場合も従来どおり公開を試みるが、状況をログに残す
        print(f"  ⚠️ コンテナ未完了のまま公開を試行（最終状態: {status or '不明'}）", file=sys.stderr)

    r = requests.post(f"{META_API}/{ig_user_id}/media_publish", data={
        "creation_id": creation_id,
        "access_token": META_TOKEN,
    }, timeout=30)
    if not r.ok:
        # raise_for_status だとエラー本文が LINE 通知に載らないため、本文込みで投げる
        raise Exception(f"publish失敗: {r.status_code} {r.text[:400]}")
    return r.json()["id"]


# ── 既投稿判定（Meta API側） ──────────────────────────────────
def already_posted_today(ig_user_id: str) -> bool:
    """今日(JST)すでにストーリー投稿があれば True。API失敗時は False（fail-open）。"""
    try:
        r = requests.get(f"{META_API}/{ig_user_id}/stories", params={
            "fields": "id,timestamp",
            "access_token": META_TOKEN,
        }, timeout=15)
        if not r.ok:
            print(f"stories取得失敗（投稿継続）: {r.status_code} {r.text[:200]}", file=sys.stderr)
            return False
        today_jst = datetime.now(JST).date()
        for story in r.json().get("data", []):
            ts = story.get("timestamp", "")
            try:
                story_dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                if story_dt.astimezone(JST).date() == today_jst:
                    print(f"既存ストーリー検出: id={story['id']} timestamp={ts}")
                    return True
            except Exception:
                continue
        return False
    except Exception as e:
        print(f"stories取得例外（投稿継続）: {e}", file=sys.stderr)
        return False
