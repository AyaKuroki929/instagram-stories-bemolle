"""@bemolle_diet Instagram Stories 自動投稿パッケージ

post_story.py（1ファイル1384行）を役割ごとに分割したもの：

  config.py     … 環境変数・定数（全設定を1箇所に）
  state.py      … JSON状態ファイル管理（used_photos / recent_texts / 最終投稿マーカー）
  photos.py     … 写真選択：ahash・顔検出・シリーズ判定・Drive取得・予備写真
  content.py    … 文章生成：Claude呼び出し・天気・季節
  images.py     … 画像生成：フォント・サロン用ストーリー画像
  publisher.py  … 投稿：画像アップロード（imgbb/Blob）・Stories投稿・既投稿判定
  auth.py       … Metaトークン管理・GitHub Secret更新
  notify.py     … LINE通知
  threads.py    … Threads連携：投稿取得・Threads風画像・Threads→ストーリー実行
  main.py       … 全体の流れ（エントリーポイント）

GitHub Actions からは従来どおり `python post_story.py` で起動する。
"""
