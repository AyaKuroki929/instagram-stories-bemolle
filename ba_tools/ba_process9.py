"""方式A対応 BA処理。頭を首の中間で切り落とし（顔ピクセルを完全除去）、
各Canva枠の正確なアスペクト比に合わせて合成する。update_fillのcoverで1:1一致。

usage: python3 ba_process9.py <before.jpg> <after.jpg> <out_dir> [neck_frac]
出力: b_p2.png(0.4501) b_cov.png(0.3505) a_p3.png(0.4791) a_cov.png(0.2947)
"""
import sys, os, numpy as np
from PIL import Image, ImageOps, ImageFilter
from rembg import remove, new_session
from scipy import ndimage
import mediapipe as mp

SESS = new_session("isnet-general-use")  # 脇の隙間を正しく抜く(u2net_human_segは脇下を塞ぐ誤り)
POSE = mp.solutions.pose.Pose(static_image_mode=True, model_complexity=2)

# 各フレームのアスペクト比(width/height) — read-designの枠実測から
FRAMES = {
    "b_p2":  0.45012,   # ページ2 before単体
    "b_cov": 0.35053,   # ページ1 表紙 before(細い)
    "a_p3":  0.47907,   # ページ3 after単体
    "a_cov": 0.29470,   # ページ1 表紙 after(細い)
}
T = 1300.0              # 肩-かかとの目標ピクセル(before/afterで身体倍率を揃える)
NECK_FRAC = 0.45       # 顎→肩の何割で切るか(0=顎下,1=肩) ※mainでargv上書き
TOP_PAD = 0.05         # 首切り線の上に少し余白(枠上端で断ち切れに見せない)
BOT_PAD = 0.03         # 足元の下に少し余白


def load_upright(path):
    im = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    if im.width > im.height:
        im = im.rotate(-90, expand=True)
    return im


def clean_alpha(rgba):
    """rembgが残す半透明ハロー・孤立ノイズを除去。人体本体(最大連結成分)だけ残す。
    モルフォロジーは縮小マスク上で行い高速化(フル解像度で反復すると激重)。"""
    arr = np.array(rgba)
    a = arr[:, :, 3]
    H, W = a.shape
    MS = 640                                   # マスク処理用の縮小幅
    scale = MS / float(W)
    sh, sw = max(1, int(H * scale)), MS
    small = np.array(Image.fromarray(a).resize((sw, sh), Image.BILINEAR))
    er = max(1, sw // 130)                      # 縮小系での影の橋の太さ
    core = small > 140                          # 影(≲120)を含まない確実な人体
    core = ndimage.binary_opening(core, iterations=er)   # 細い橋を切る
    lbl, n = ndimage.label(core)
    if n >= 1:
        sizes = ndimage.sum(np.ones_like(lbl), lbl, range(1, n + 1))
        body = lbl == (int(np.argmax(sizes)) + 1)        # 最大成分=人体
    else:
        body = core
    body = ndimage.binary_dilation(body, iterations=er + 2)  # 縁を少し復元
    mask_full = np.array(Image.fromarray((body * 255).astype("uint8")).resize((W, H), Image.BILINEAR))
    new_a = np.where(mask_full > 128, a, 0)
    new_a = np.where(new_a < 30, 0, new_a).astype("uint8")   # 薄いハローを消す
    arr[:, :, 3] = new_a
    out = Image.fromarray(arr)
    r, g, b, al = out.split()
    al = al.filter(ImageFilter.GaussianBlur(0.8))            # 縁を軽く滑らかに
    return Image.merge("RGBA", (r, g, b, al))


def process_one(path):
    up = load_upright(path)
    rgba = clean_alpha(remove(up, session=SESS).convert("RGBA"))
    arr = np.array(rgba)
    H, W = arr.shape[:2]
    r = POSE.process(np.array(up))
    if not r.pose_landmarks:
        raise SystemExit(f"pose未検出: {path}")
    L = r.pose_landmarks.landmark
    def Y(i): return L[i].y * H
    eyes_y = np.mean([Y(2), Y(5)])
    mouth_y = np.mean([Y(9), Y(10)])
    face_h = max(1.0, mouth_y - eyes_y)
    chin = mouth_y + face_h * 0.8
    sh = min(Y(11), Y(12))                 # 肩
    heel = max(Y(29), Y(30), Y(31), Y(32)) # かかと
    neck_cut = chin + (sh - chin) * NECK_FRAC
    neck_cut = min(neck_cut, sh)           # 念のため肩より下げない
    ref = heel - sh                        # 肩-かかと=身体倍率の基準

    a = arr[:, :, 3]
    ys, xs = np.where(a > 12)
    x0, x1 = xs.min(), xs.max() + 1
    top = int(max(0, neck_cut))            # ここから上(=頭・顔)を捨てる
    bot = min(H, int(heel) + int((heel - sh) * BOT_PAD) + 6)
    body = Image.fromarray(arr[top:bot, x0:x1])  # 首の中間〜足元
    return body, ref


def body_mean(rgba):
    arr = np.array(rgba).astype(float); m = arr[:, :, 3] > 40
    return arr[:, :, :3][m].mean(axis=0) if m.sum() else np.array([128., 128, 128])


def match_color(after, before):
    bm, am = body_mean(before), body_mean(after)
    gain = np.clip(bm / np.maximum(am, 1e-3), 0.85, 1.15)
    lg = bm.mean() / max(am.mean(), 1e-3)
    if lg > 1.05: gain *= 1.05 / lg
    arr = np.array(after).astype(float)
    for c in range(3):
        arr[:, :, c] = np.clip(arr[:, :, c] * gain[c], 0, 255)
    return Image.fromarray(arr.astype("uint8"))


def compose(body, aspect):
    """body(首〜足)を、指定アスペクトのキャンバスへ。高さいっぱい・足元下寄せ。
    枠より広ければ左右中央クロップ、狭ければ左右bg余白。透明=ページ背景が出る。"""
    bw, bh = body.size
    inner_h = bh
    canvas_h = int(round(inner_h * (1 + TOP_PAD + BOT_PAD)))
    canvas_w = int(round(canvas_h * aspect))
    canvas = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
    y = int(round(canvas_h * TOP_PAD))
    x = (canvas_w - bw) // 2                # 負ならクロップ・正ならbg余白
    canvas.alpha_composite(body, (x, y))
    return canvas


def build(before_path, after_path, out, neck_frac=None):
    """before/after写真→4枚の枠別head-off PNG。生成パスのdictを返す。"""
    if neck_frac is not None:
        globals()["NECK_FRAC"] = neck_frac
    os.makedirs(out, exist_ok=True)
    before, rb = process_one(before_path)
    after, ra = process_one(after_path)
    before = before.resize((max(1, int(before.width * T / rb)), max(1, int(before.height * T / rb))))
    after = after.resize((max(1, int(after.width * T / ra)), max(1, int(after.height * T / ra))))
    after = match_color(after, before)
    paths = {}
    for key, aspect in FRAMES.items():
        src = before if key.startswith("b_") else after
        p = os.path.join(out, key + ".png")
        compose(src, aspect).save(p)
        paths[key] = p
    return paths


def main():
    nf = float(sys.argv[4]) if len(sys.argv) > 4 else NECK_FRAC
    paths = build(sys.argv[1], sys.argv[2], sys.argv[3], nf)
    print("OK neck_frac", nf, "->", list(paths))


if __name__ == "__main__":
    main()
