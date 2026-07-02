"""Metaトークン管理（自動延長 → 失敗時はLINE警告）と GitHub Secret 更新"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

import requests

from .config import JST, META_API, META_TOKEN
from .notify import notify


def update_github_secret(name: str, value: str, pat: str) -> None:
    """GitHub Actions Secret を libsodium 暗号化で更新する"""
    from base64 import b64encode
    from nacl import encoding, public
    repo = os.environ.get("GITHUB_REPOSITORY", "AyaKuroki929/instagram-stories-bemolle")
    h = {"Authorization": f"token {pat}", "Accept": "application/vnd.github+json"}
    r = requests.get(
        f"https://api.github.com/repos/{repo}/actions/secrets/public-key",
        headers=h, timeout=10,
    )
    r.raise_for_status()
    pk_data = r.json()
    pk = public.PublicKey(pk_data["key"].encode("utf-8"), encoding.Base64Encoder())
    sealed_box = public.SealedBox(pk)
    encrypted = b64encode(sealed_box.encrypt(value.encode("utf-8"))).decode("utf-8")
    r = requests.put(
        f"https://api.github.com/repos/{repo}/actions/secrets/{name}",
        headers=h,
        json={"encrypted_value": encrypted, "key_id": pk_data["key_id"]},
        timeout=10,
    )
    r.raise_for_status()


def renew_meta_token(app_id: str, app_secret: str, gh_pat: str) -> None:
    """fb_exchange_tokenで延長 → GitHub Secret更新 → LINE通知"""
    r = requests.get(f"{META_API}/oauth/access_token", params={
        "grant_type": "fb_exchange_token",
        "client_id": app_id,
        "client_secret": app_secret,
        "fb_exchange_token": META_TOKEN,
    }, timeout=15)
    r.raise_for_status()
    new_token = r.json().get("access_token")
    if not new_token:
        raise Exception("新トークンがレスポンスにない")
    if new_token == META_TOKEN:
        print("自動更新: トークンに変化なし（延長未対応の可能性）")
        return

    v = requests.get(f"{META_API}/debug_token", params={
        "input_token": new_token,
        "access_token": new_token,
    }, timeout=10)
    new_expires = v.json().get("data", {}).get("expires_at", 0) if v.ok else 0
    new_remaining = int((new_expires - datetime.now(timezone.utc).timestamp()) / 86400) if new_expires else 0

    update_github_secret("META_ACCESS_TOKEN_STORIES", new_token, gh_pat)
    notify(f"✅ Meta access token 自動更新完了\n新期限: 残り{new_remaining}日")
    print(f"トークン自動更新完了: 残り{new_remaining}日")


def manage_meta_token() -> None:
    """期限<=30日で自動延長を試行。失敗or準備未済なら、<=14日でLINE警告。"""
    try:
        r = requests.get(f"{META_API}/debug_token", params={
            "input_token": META_TOKEN,
            "access_token": META_TOKEN,
        }, timeout=10)
        if not r.ok:
            return
        data = r.json().get("data", {})
        app_id = data.get("app_id")
        expires_at = data.get("expires_at", 0)
        if not expires_at:
            print("トークン期限: 無期限")
            return
        remaining = (expires_at - datetime.now(timezone.utc).timestamp()) / 86400
        print(f"トークン期限: 残り{int(remaining)}日")
        if remaining > 30:
            return

        # 自動更新を試行
        app_secret = os.environ.get("META_APP_SECRET")
        gh_pat = os.environ.get("GH_PAT")
        if app_secret and gh_pat and app_id:
            try:
                renew_meta_token(app_id, app_secret, gh_pat)
                return
            except Exception as e:
                print(f"自動更新失敗（LINE警告にフォールバック）: {e}", file=sys.stderr)

        # 自動更新不可 or 失敗 → 残り14日以下なら手動催促
        if remaining <= 14:
            expiry_jst = datetime.fromtimestamp(expires_at, tz=timezone.utc).astimezone(JST)
            notify(
                f"⚠️ Meta access token 期限間近\n"
                f"残り {int(remaining)} 日（期限: {expiry_jst.strftime('%Y-%m-%d %H:%M')} JST）\n\n"
                f"自動更新が動かないので手動再発行が必要です:\n"
                f"1. business.facebook.com/settings/system_users\n"
                f"2. bemolle-storiesbot → トークンを生成\n"
                f"3. BemolleStories / 60日間 / 5権限 → 生成\n"
                f"4. GitHub Secret META_ACCESS_TOKEN_STORIES に貼り付け"
            )
    except Exception as e:
        print(f"トークン管理失敗（投稿継続）: {e}", file=sys.stderr)
