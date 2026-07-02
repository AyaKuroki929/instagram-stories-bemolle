#!/usr/bin/env python3
"""@bemolle_diet Instagram Stories 自動投稿（エントリーポイント）

実装は story/ パッケージに役割ごとに分割済み：
  config / state / photos / content / images / publisher / auth / notify / threads / main
GitHub Actions からは従来どおり `python post_story.py` で起動する。
"""
from story.main import main

if __name__ == "__main__":
    main()
