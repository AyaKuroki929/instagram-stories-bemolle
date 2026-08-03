# -*- coding: utf-8 -*-
"""テキスト配置の回帰テスト（2026-08-03 コース一覧衝突事故の再発防止・Sol指摘で追加）
実行: META_ACCESS_TOKEN=x ANTHROPIC_API_KEY=x IMGBB_API_KEY=x LINE_CHANNEL_ACCESS_TOKEN=x python3 -m pytest tests/test_layout.py -q
（pytestが無ければ python3 tests/test_layout.py で簡易実行）
"""
import os, sys, io
os.environ.setdefault("META_ACCESS_TOKEN","x"); os.environ.setdefault("ANTHROPIC_API_KEY","x")
os.environ.setdefault("IMGBB_API_KEY","x"); os.environ.setdefault("LINE_CHANNEL_ACCESS_TOKEN","x")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from PIL import Image
from datetime import datetime
from story import images
from story.config import JST
from story.images import _course_geometry

def _bg(color=(180,170,165)):
    buf=io.BytesIO(); Image.new("RGB",(1080,1920),color).save(buf,format="JPEG"); return buf.getvalue()

def _run(faces, courses, closing="通うたびに表情が明るくなっていく、その瞬間が嬉しいです。本日のご来店をお待ちしております。",
         layout=None, probe={}):
    images.get_drive_photo=lambda pool,names=None: _bg()
    images.detect_faces=lambda im: faces
    content={"greeting":"おはようございます。","status":"本日もリピーター様、ご新規様で満席となっております。",
             "closing":closing,"courses":courses}
    if layout: content["layout"]=layout
    # y0 を観測するため draw をフック…の代わりに、内部変数をログで拾うのは面倒なので
    # ImageDraw.text の最初の呼び出しyを記録する
    ys=[]
    orig=images.ImageDraw.Draw
    class Rec:
        def __init__(self,d): self.d=d
        def text(self,xy,*a,**k): ys.append(xy[1]); return self.d.text(xy,*a,**k)
        def __getattr__(self,n): return getattr(self.d,n)
    images.ImageDraw.Draw=lambda im: Rec(orig(im))
    try:
        images.build_image(content, datetime(2026,8,3,7,0,tzinfo=JST))
    finally:
        images.ImageDraw.Draw=orig
    probe["first_text_y"]=min(ys) if ys else None
    probe["text_ys"]=ys
    return probe

def test_small_face_ignored_top():
    p=_run(faces=[(500,650,46,46)], courses=["A","B","C"])
    assert p["first_text_y"]==300, p  # 46pxの誤検出では動かさない＝上配置

def test_big_face_top_moves_bottom_above_courses():
    p=_run(faces=[(400,300,200,200)], courses=["A","B","C"])
    _,_,course_top=_course_geometry(3)
    body=[y for y in p["text_ys"] if y<course_top]  # 本文はコース上端より上で完結
    assert p["first_text_y"]>300, p
    assert max(body)<course_top-20, p

def test_long_text_bottom_stays_above_courses():
    # 長文でも下配置が選ばれた場合は、本文がコース一覧の上で完結すること
    long_closing="通うたびに表情が明るくなっていくのを見るのが嬉しいです。"*4 + "本日のご来店をお待ちしております。"
    p=_run(faces=[(400,300,200,200)], courses=["A","B","C"], closing=long_closing)
    _,_,course_top=_course_geometry(3)
    body=[y for y in p["text_ys"] if y<course_top]
    assert max(body)+60<=course_top, p  # 最終行の下端（+行高相当）がコース上端より上

def test_extreme_text_falls_back_to_top():
    # 下に入り切らない極端な長文は下候補を作らない→上配置
    huge="通うたびに表情が明るくなっていくのを見るのが嬉しいです。"*10
    p=_run(faces=[(400,300,200,200)], courses=["A","B","C"], closing=huge)
    assert p["first_text_y"]==300, p

def test_monthly_lower_without_courses():
    p=_run(faces=[], courses=[], layout="lower", closing="9月も、皆様の綺麗のお手伝いをさせてください。心よりお待ちしております。")
    assert p["first_text_y"]>800, p  # 従来のlower（1660-total起点）

def test_faces_none_fallback():
    p=_run(faces=None, courses=["A","B","C"])
    assert p["first_text_y"]==300, p

if __name__=="__main__":
    for n,f in sorted(globals().items()):
        if n.startswith("test_"):
            f(); print(f"ok {n}")
    print("ALL OK")
