"""環境変数・定数（全設定を1箇所に集約）

必須の環境変数が無い場合は import 時に KeyError で落ちる（従来と同じ挙動）。
"""
from __future__ import annotations

import os
from datetime import timezone, timedelta

# ── 設定 ──────────────────────────────────────────────────────────
META_TOKEN    = os.environ["META_ACCESS_TOKEN"]
ANTHROPIC_KEY = os.environ["ANTHROPIC_API_KEY"]
IMGBB_KEY      = os.environ["IMGBB_API_KEY"]
BLOB_TOKEN     = os.environ.get("BLOB_READ_WRITE_TOKEN", "")  # Vercel Blob（主ホスト・未設定ならimgbbのみ）
LINE_TOKEN     = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
GDRIVE_REFRESH = os.environ.get("GOOGLE_REFRESH_TOKEN", "")
GDRIVE_CLIENT  = os.environ.get("GOOGLE_CLIENT_ID", "")
GDRIVE_SECRET  = os.environ.get("GOOGLE_CLIENT_SECRET", "")
USED_PHOTOS_FILE     = "used_photos.json"
LAST_POST_FILE       = "last_post.json"   # 最終投稿日マーカー（Meta API非依存の二重投稿防止・第2の砦）
LAST_POST_THREADS_FILE = "last_post_threads.json"  # Threads→ストーリー用の最終投稿日マーカー（サロン投稿と独立）
RECENT_TEXTS_FILE    = "recent_texts.json"  # 最近の挨拶・締め文の履歴（書き出しの連日重複を防ぐ）
THREADS_TOKEN        = os.environ.get("THREADS_API_TOKEN_BEMOLLE", "")  # ベモーレThreads（threadsモードのみ必須・threads-botと同名）
THREADS_API          = "https://graph.threads.net/v1.0"
FALLBACK_DIR         = "fallback_photos"  # Drive障害時に使う実写真キャッシュ（複数枚・グラデ背景を出さないため）
FALLBACK_MAX         = 5                   # 予備写真の最大保持数（達したら打ち止め＝git肥大化防止）
COOLDOWN_DAYS        = 14  # 同じ写真を使わない日数
SIMILARITY_DAYS      = 7   # 類似写真を避ける日数（直近1週間と見比べる）
SERIES_DAYS          = 4   # 同じ撮影シリーズ（mur_等）を避ける日数。ahash/dhashでは捉えられない「同じ撮影の似た構図」を回避
SIMILARITY_THRESHOLD = 12  # ahashのハミング距離（64ビット中・これ以下を「似ている」と判定）。実測で酷似ペア=9・別写真=20以上のため中間の12に設定
GDRIVE_FOLDER        = "18K4hZUjbBH3V1XJjiSNNfss6GZnaTNqV"  # ベモーレ ストーリー素材（ルート）
GDRIVE_FOLDER_SLIM   = "170R8MxD_ByugDmxctVQbpmY2p3nXVDK8"  # 痩身
GDRIVE_FOLDER_FACIAL = "1DwNv1e5_j4YnDt23DNgYp9RatJQYpGtj"  # 肌質改善
GDRIVE_FOLDER_COMMON = "18eBpPM72QvZrlVwCAmeenfq6pNjoIEQy"   # 共通（部屋・内装など汎用）
IG_USER_ID     = os.environ.get("IG_USER_ID", "17841470478859455")
META_API       = "https://graph.facebook.com/v25.0"
JST            = timezone(timedelta(hours=9))

FONT_PATHS = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJKjp-Regular.otf",
    "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",
]

COURSES_SLIM   = ["全身痩身12回コース", "全身痩身18回コース", "全身痩身24回コース"]
COURSES_FACIAL = ["３ヶ月肌質改善プログラム", "６ヶ月肌質改善プログラム"]
COURSES_TRIAL  = ["全身痩身体験", "肌質改善体験"]
