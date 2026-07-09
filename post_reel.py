"""Instagramリール自動投稿

Google Drive「マイドライブ/リール/投稿キュー」に置かれた
  ・動画（.mp4）
  ・表紙（.png / .jpg）
  ・キャプション（.txt）
の3点セットを取得し、Vercel Blobで公開URL化して
Meta Graph APIでリール投稿（カバー・キャプション付き）する。

投稿成功後はキュー内のファイルを「投稿済み」フォルダへ移動し、
二重投稿を防ぐ。結果はLINEで通知する。

GitHub Actions（post_reel.yml）から手動起動で実行する想定。
"""
from __future__ import annotations

import random
import sys
import time

import requests

from story.config import (
    BLOB_TOKEN,
    GDRIVE_CLIENT,
    GDRIVE_REFRESH,
    GDRIVE_SECRET,
    IG_USER_ID,
    META_API,
    META_TOKEN,
)
from story.notify import notify

QUEUE_FOLDER_NAME = "投稿キュー"
DONE_FOLDER_NAME = "投稿済み"
REEL_PARENT_NAME = "リール"


# ── Google Drive ──────────────────────────────────────────────
def drive_token() -> str:
    r = requests.post("https://oauth2.googleapis.com/token", data={
        "grant_type": "refresh_token",
        "refresh_token": GDRIVE_REFRESH,
        "client_id": GDRIVE_CLIENT,
        "client_secret": GDRIVE_SECRET,
    }, timeout=15)
    r.raise_for_status()
    return r.json()["access_token"]


def find_folder(headers: dict, name: str, parent_id: str | None = None) -> str | None:
    q = f"name='{name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
    if parent_id:
        q += f" and '{parent_id}' in parents"
    r = requests.get("https://www.googleapis.com/drive/v3/files",
                     headers=headers, params={"q": q, "fields": "files(id,name)"}, timeout=15)
    r.raise_for_status()
    files = r.json().get("files", [])
    return files[0]["id"] if files else None


def ensure_folder(headers: dict, name: str, parent_id: str) -> str:
    fid = find_folder(headers, name, parent_id)
    if fid:
        return fid
    r = requests.post("https://www.googleapis.com/drive/v3/files", headers=headers, json={
        "name": name,
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [parent_id],
    }, timeout=15)
    r.raise_for_status()
    return r.json()["id"]


def list_queue(headers: dict, folder_id: str) -> list[dict]:
    r = requests.get("https://www.googleapis.com/drive/v3/files", headers=headers, params={
        "q": f"'{folder_id}' in parents and trashed=false",
        "fields": "files(id,name,mimeType,createdTime)",
        "orderBy": "createdTime desc",
    }, timeout=15)
    r.raise_for_status()
    return r.json().get("files", [])


def download(headers: dict, file_id: str) -> bytes:
    r = requests.get(f"https://www.googleapis.com/drive/v3/files/{file_id}",
                     headers=headers, params={"alt": "media"}, timeout=180)
    r.raise_for_status()
    return r.content


def move_to_done(headers: dict, file_id: str, queue_id: str, done_id: str) -> None:
    requests.patch(f"https://www.googleapis.com/drive/v3/files/{file_id}",
                   headers=headers,
                   params={"addParents": done_id, "removeParents": queue_id},
                   timeout=15)


# ── Vercel Blob ───────────────────────────────────────────────
def upload_blob(data: bytes, ext: str, content_type: str) -> str:
    pathname = f"reel-{int(time.time())}-{random.randint(1000, 9999)}.{ext}"
    r = requests.put(
        "https://vercel.com/api/blob/",
        params={"pathname": pathname},
        headers={
            "authorization": f"Bearer {BLOB_TOKEN}",
            "x-api-version": "12",
            "x-content-type": content_type,
            "x-add-random-suffix": "1",
            "x-vercel-blob-access": "public",
        },
        data=data,
        timeout=300,
    )
    if not r.ok:
        raise Exception(f"Blob {r.status_code}: {r.text[:250]}")
    url = r.json().get("url")
    if not url:
        raise Exception(f"Blob応答異常（URLなし）: {r.text[:250]}")
    return url


# ── Instagram Reels 投稿 ──────────────────────────────────────
def post_reel(video_url: str, cover_url: str, caption: str) -> str:
    r = requests.post(f"{META_API}/{IG_USER_ID}/media", data={
        "media_type": "REELS",
        "video_url": video_url,
        "cover_url": cover_url,
        "caption": caption,
        "share_to_feed": "true",
        "access_token": META_TOKEN,
    }, timeout=60)
    if not r.ok:
        raise Exception(f"コンテナ作成失敗: {r.status_code} {r.text[:400]}")
    creation_id = r.json()["id"]
    print(f"コンテナ作成: {creation_id}")

    # 動画処理の完了を待つ（最大8分）
    status = ""
    for attempt in range(48):
        time.sleep(10)
        s = requests.get(f"{META_API}/{creation_id}", params={
            "fields": "status_code",
            "access_token": META_TOKEN,
        }, timeout=15)
        if s.ok:
            status = s.json().get("status_code", "")
            print(f"  処理状態: {status}（{(attempt + 1) * 10}秒）")
            if status == "FINISHED":
                break
            if status == "ERROR":
                raise Exception("動画処理エラー（Instagram側）。動画仕様を確認してください")
    if status != "FINISHED":
        raise Exception(f"8分待っても処理が完了しませんでした（最終状態: {status or '不明'}）")

    r = requests.post(f"{META_API}/{IG_USER_ID}/media_publish", data={
        "creation_id": creation_id,
        "access_token": META_TOKEN,
    }, timeout=60)
    if not r.ok:
        raise Exception(f"publish失敗: {r.status_code} {r.text[:400]}")
    return r.json()["id"]


def get_permalink(media_id: str) -> str:
    try:
        r = requests.get(f"{META_API}/{media_id}", params={
            "fields": "permalink",
            "access_token": META_TOKEN,
        }, timeout=15)
        return r.json().get("permalink", "") if r.ok else ""
    except Exception:
        return ""


# ── main ──────────────────────────────────────────────────────
def main() -> None:
    headers = {"Authorization": f"Bearer {drive_token()}"}

    reel_parent = find_folder(headers, REEL_PARENT_NAME)
    if not reel_parent:
        raise Exception("Driveに「リール」フォルダが見つかりません")
    queue_id = find_folder(headers, QUEUE_FOLDER_NAME, reel_parent)
    if not queue_id:
        raise Exception("Driveに「リール/投稿キュー」フォルダが見つかりません")

    files = list_queue(headers, queue_id)
    video = next((f for f in files if f["name"].lower().endswith(".mp4")), None)
    cover = next((f for f in files if f["name"].lower().endswith((".png", ".jpg", ".jpeg"))), None)
    caption_file = next((f for f in files if f["name"].lower().endswith(".txt")), None)

    if not video:
        print("投稿キューに動画がありません。終了します。")
        return
    if not cover or not caption_file:
        raise Exception(f"3点セットが揃っていません（動画:{bool(video)} 表紙:{bool(cover)} キャプション:{bool(caption_file)}）")

    print(f"動画: {video['name']} / 表紙: {cover['name']} / キャプション: {caption_file['name']}")

    video_bytes = download(headers, video["id"])
    cover_bytes = download(headers, cover["id"])
    caption = download(headers, caption_file["id"]).decode("utf-8")
    print(f"取得完了（動画 {len(video_bytes) // 1024 // 1024}MB / キャプション {len(caption)}字）")

    video_url = upload_blob(video_bytes, "mp4", "video/mp4")
    cover_ext = "png" if cover["name"].lower().endswith(".png") else "jpg"
    cover_url = upload_blob(cover_bytes, cover_ext, f"image/{'png' if cover_ext == 'png' else 'jpeg'}")
    print(f"公開URL化完了\n  video: {video_url}\n  cover: {cover_url}")

    media_id = post_reel(video_url, cover_url, caption)
    permalink = get_permalink(media_id)
    print(f"投稿完了: media_id={media_id} {permalink}")

    # キューを空にして二重投稿を防ぐ
    done_id = ensure_folder(headers, DONE_FOLDER_NAME, reel_parent)
    for f in (video, cover, caption_file):
        move_to_done(headers, f["id"], queue_id, done_id)
    print("キューのファイルを「投稿済み」へ移動しました")

    notify(f"🎬 リール投稿が完了しました！\n{video['name']}\n{permalink or '(URL取得なし)'}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"エラー: {e}", file=sys.stderr)
        notify(f"🚨 リール投稿に失敗しました\n{str(e)[:300]}\nGitHub Actionsのログを確認してください。")
        sys.exit(1)
