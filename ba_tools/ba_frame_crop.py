"""before/afterを同一倍率(肩-かかと=T)に揃え、各Canva枠のアスペクトでクロップする。
背景は残す（Canvaの「背景透過」で後から抜く運用）。頭はあご位置で切り落とし顔を出さない。
★肩・腕を絶対に切らない: isnetシルエットで体の左右端を測り、枠幅が足りなければ体を小さくして必ず全幅を収める。

usage: python3 ba_frame_crop.py <before> <after> <out_dir> [neck_frac]
出力: b_p2.jpg b_cov.jpg a_p3.jpg a_cov.jpg （背景つき・枠比一致・肩まで全部入る）
"""
import sys, os, numpy as np
from PIL import Image, ImageOps
from rembg import remove, new_session
import mediapipe as mp

# (aspect, which, extra_top): extra_top=枠が上を削る保険の追加余白(対 肩-かかと)。表紙(cov)は枠が上を削るので多め。
FRAMES = {"b_p2": (0.45012, "b", 0.0), "b_cov": (0.35053, "b", 0.0),
          "a_p3": (0.47907, "a", 0.0), "a_cov": (0.29470, "a", 0.0)}
T = 1300.0                                  # 肩-かかとの共通目標px
SHOULDER_ABOVE = float(sys.argv[4]) if len(sys.argv) > 4 else 0.055  # 肩関節の何割上で切るか(肩全部入る・顔出ない・見本一致)
BOT_PAD = 0.05                              # 足元の下の余白(対 身体高)
SIDE_PAD = 0.07                             # 体の左右に足す余白(絶対に切らない保険)


def grade(im):
    """血色を出す: 明るさ＋暖色(R上げB下げ)＋軽い彩度アップ。before/afterに同じ処理で色を揃える。"""
    arr = np.array(im).astype(float)
    arr = arr * 1.05 + 7                              # 明るさ
    arr[:, :, 0] *= 1.06                              # 赤(血色)
    arr[:, :, 2] *= 0.95                              # 青を下げて暖色に
    g = arr.mean(2, keepdims=True)
    arr = g + (arr - g) * 1.12                        # 彩度
    return Image.fromarray(np.clip(arr, 0, 255).astype("uint8"))
POSE = mp.solutions.pose.Pose(static_image_mode=True, model_complexity=2)
SEG = new_session("isnet-general-use")


def load_upright(path):
    im = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    if im.width > im.height:
        im = im.rotate(-90, expand=True)
    return im


def measure(im):
    W, H = im.size
    L = POSE.process(np.array(im)).pose_landmarks.landmark
    def Y(i): return L[i].y * H
    mouth = np.mean([Y(9), Y(10)]); eyes = np.mean([Y(2), Y(5)]); fh = max(1, mouth - eyes)
    chin = mouth + fh * 0.8
    sh = min(Y(11), Y(12)); heel = max(Y(29), Y(30), Y(31), Y(32))
    alpha = np.array(remove(im, session=SEG).convert("RGBA"))[:, :, 3]
    rows = (alpha > 40).sum(1)                       # 各行の体の幅
    # 切り位置=肩関節のSHOULDER_ABOVEぶん上(顔と肩の間の窓)。顔は出ず肩は全部入る。
    neck = sh - (heel - sh) * SHOULDER_ABOVE
    yy = np.arange(H)[:, None]
    ys, xs = np.where((alpha > 40) & (yy >= neck))    # 表示される体の左右端
    lx, rx = xs.min(), xs.max()
    return {"sh": sh, "heel": heel, "neck": neck, "ref": heel - sh,
            "bcx": (lx + rx) / 2.0, "bw": float(rx - lx)}, alpha


def body_mean(im, alpha):
    arr = np.array(im).astype(float); m = alpha > 60
    return arr[m].reshape(-1, 3).mean(0) if m.sum() else np.array([128., 128, 128])


def main():
    out = sys.argv[3]; os.makedirs(out, exist_ok=True)
    b = load_upright(sys.argv[1]); a = load_upright(sys.argv[2])
    mb, alb = measure(b); ma, ala = measure(a)
    sb = T / mb["ref"]; sa = T / ma["ref"]           # 肩-かかと=Tへ正規化
    def rs(im, s): return im.resize((max(1, int(im.width * s)), max(1, int(im.height * s))))
    b = grade(rs(b, sb)); a = grade(rs(a, sa))       # 血色調整＋正規化
    alb = np.array(Image.fromarray(alb).resize(b.size))
    ala = np.array(Image.fromarray(ala).resize(a.size))
    # afterの肌色をbeforeに合わせる（右2枚が薄い対策）
    bm = body_mean(b, alb); am = body_mean(a, ala)
    gain = np.clip(bm / np.maximum(am, 1e-3), 0.82, 1.22)
    aarr = np.array(a).astype(float)
    for c in range(3):
        aarr[:, :, c] *= gain[c]
    a = Image.fromarray(np.clip(aarr, 0, 255).astype("uint8"))
    for m, s in ((mb, sb), (ma, sa)):
        for k in ("sh", "heel", "neck", "bcx", "bw"):
            m[k] *= s
    body_h = max(mb["heel"] - mb["neck"], ma["heel"] - ma["neck"])  # 長い方に合わせる=どちらも肩が切れない

    def crop_one(im, m, aspect, extra_top):
        W, H = im.size
        crop_h = body_h * (1 + BOT_PAD)          # 全枠で共通=体の大きさを揃える(狭い表紙は左右をクロップ)
        crop_w = crop_h * aspect
        top = m["neck"] - extra_top * T          # 表紙は枠に削られる分、窓ごと上へずらす(アスペクト維持)
        bot = top + crop_h
        left = m["bcx"] - crop_w / 2; right = m["bcx"] + crop_w / 2
        if left < 0: right -= left; left = 0
        if right > W: left -= (right - W); right = W
        left = max(0, left); top = max(0, top)
        return im.crop((int(left), int(top), int(min(W, right)), int(min(H, bot))))

    for key, (aspect, which, extra_top) in FRAMES.items():
        im, m = (b, mb) if which == "b" else (a, ma)
        crop_one(im, m, aspect, extra_top).save(os.path.join(out, key + ".jpg"), quality=92)
    print("OK T", int(T), "body_h", int(body_h),
          "bw_b", int(mb["bw"]), "bw_a", int(ma["bw"]))


if __name__ == "__main__":
    main()
