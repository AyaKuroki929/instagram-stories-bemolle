"""B案の切り位置検出（2026-08-15 彩さん確定ルール）：
「肩の丸み」が始まる行を輪郭から見つけ、その真上で水平カットする。丸みは1pxも削らない。

- 顎の位置から下だけを探索する（頭頂を首と誤検出しないため）
- 胴体の中心帯だけを見る（腕は肩より外なので混ざらない）
- 連続8行が閾値を超えた最初の行＝丸みの立ち上がり（ノイズ耐性）

usage(import): from ba_shoulder_cut import detect_cut ; y = detect_cut("photo.jpg")
usage(cli)   : python3 ba_shoulder_cut.py before.jpg after.jpg out_dir  # デバッグ線つき画像も出力
"""
import sys, numpy as np
from PIL import Image, ImageOps, ImageDraw
from rembg import remove, new_session
from scipy import ndimage
import mediapipe as mp

_POSE = mp.solutions.pose.Pose(static_image_mode=True, model_complexity=2)
_SEG = new_session("isnet-general-use")


def load_upright(path):
    im = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    if im.width > im.height:
        im = im.rotate(-90, expand=True)
    return im


def _largest_component(alpha):
    m = alpha > 40
    lab, n = ndimage.label(m)
    if n == 0:
        return alpha
    sizes = ndimage.sum(m, lab, range(1, n + 1))
    return np.where(lab == int(np.argmax(sizes)) + 1, alpha, 0)


def detect_cut(path, debug_out=None, tag=""):
    """肩の丸みの真上のy座標（load_upright後の画像座標）を返す。"""
    im = load_upright(path)
    W, H = im.size
    res = _POSE.process(np.array(im))
    if not res.pose_landmarks:
        raise SystemExit(f"pose未検出: {path}")
    L = res.pose_landmarks.landmark
    X = lambda i: L[i].x * W
    Y = lambda i: L[i].y * H
    sh = min(Y(11), Y(12))
    cx = (X(11) + X(12)) / 2.0
    shoulder_w = abs(X(11) - X(12))
    alpha = _largest_component(np.array(remove(im, session=_SEG).convert("RGBA"))[:, :, 3])
    half = max(20, int(shoulder_w * 0.5))
    band = alpha[:, max(0, int(cx - half)):min(W, int(cx + half))]
    w = (band > 40).sum(1).astype(float)
    ys = np.where(w > 0)[0]
    head_top = int(ys.min())
    mouth = np.mean([Y(9), Y(10)]); eyes = np.mean([Y(2), Y(5)])
    chin = int(mouth + max(1.0, mouth - eyes) * 0.8)      # 顎
    lo = max(head_top + 1, chin); hi = max(lo + 3, int(sh))
    ymin = lo + int(np.argmin(w[lo:hi]))                   # 首（最も細い行）
    thr = w[ymin] * 1.12 + 3
    start = None
    for y in range(ymin, int(sh) + 1):
        if all(w[min(y + k, len(w) - 1)] > thr for k in range(8)):
            start = y; break
    if start is None:
        start = int(sh)
    cut = max(0, start - 2)                                # 丸みの真上
    if debug_out:
        d = im.copy(); dr = ImageDraw.Draw(d)
        dr.line([(0, cut), (W, cut)], fill=(255, 0, 0), width=8)      # 赤=切り口
        dr.line([(0, ymin), (W, ymin)], fill=(0, 160, 255), width=4)  # 青=首
        dr.line([(0, sh), (W, sh)], fill=(0, 200, 0), width=4)        # 緑=肩関節
        d.save(f"{debug_out}/cut_{tag or 'x'}.jpg", quality=85)
    return {"cut": cut, "chin": chin, "neck": ymin, "shoulder": int(sh),
            "heel": int(max(Y(29), Y(30), Y(31), Y(32))), "size": (W, H)}


if __name__ == "__main__":
    out = sys.argv[3] if len(sys.argv) > 3 else "."
    for p, t in ((sys.argv[1], "B"), (sys.argv[2], "A")):
        print(t, detect_cut(p, debug_out=out, tag=t))
