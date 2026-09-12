# -*- coding: utf-8 -*-
"""士源帳本（/m/ledger.html）主畫面 icon：無限環（lemniscate）金→薄荷漸層線條、深綠底。

owner 2026-09-13「icon 可以拿無限環設計一個」。只用 Pillow（不用 SVG 轉檔器）；
粗線用密集圓點鋪（Pillow 的 line 逐段接會有鋸齒毛邊）。
用法：.venv/Scripts/python.exe scripts/make_ledger_icon.py  → frontend/m/icon-ledger-{180,192,512}.png
"""
import math
import os

from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
S = 2048                      # 高解析畫布，最後縮圖抗鋸齒
BG = (16, 60, 36)             # 深綠底（跟 L1 的底色同系）
BG2 = (10, 40, 26)            # 底部稍深，做一點縱向漸層
C0 = (52, 211, 153)           # 薄荷綠（tabbar 綠）
C1 = (250, 204, 21)           # 金（帳本／錢）
STROKE = int(S * 0.085)


def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def lemniscate(t, a):
    s, c = math.sin(t), math.cos(t)
    d = 1 + s * s
    return a * c / d, a * s * c / d


def render() -> Image.Image:
    img = Image.new("RGBA", (S, S))
    draw = ImageDraw.Draw(img)
    # 背景縱向漸層＋圓角（iOS 會自己遮罩；圓角只是讓預覽好看）
    for y in range(S):
        draw.line([(0, y), (S, y)], fill=lerp(BG, BG2, y / S) + (255,))
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, S - 1, S - 1], radius=int(S * 0.22), fill=255)
    img.putalpha(mask)

    cx, cy, a = S / 2, S * 0.47, S * 0.36
    n = 6000
    pts = [lemniscate(2 * math.pi * i / n, a) for i in range(n + 1)]

    # 先鋪一層半透明深色光暈（讓線在亮處也有邊界），再鋪主線
    halo = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    hd = ImageDraw.Draw(halo)
    for i in range(0, n, 3):
        x, y = pts[i]
        r = STROKE / 2 + S * 0.012
        hd.ellipse([cx + x - r, cy + y - r, cx + x + r, cy + y + r], fill=(0, 0, 0, 255))
    halo.putalpha(halo.split()[3].point(lambda v: int(v * 0.35)))
    img.alpha_composite(halo)
    draw = ImageDraw.Draw(img)
    for i in range(0, n, 2):
        t = i / n
        g = 1 - abs(2 * t - 1)          # 0→1→0：左葉金、右葉薄荷，環是閉的兩端接得起來
        x, y = pts[i]
        r = STROKE / 2
        draw.ellipse([cx + x - r, cy + y - r, cx + x + r, cy + y + r], fill=lerp(C0, C1, g) + (255,))
    # 交叉點提亮一顆小點（環的「結」）
    r = STROKE * 0.28
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(255, 255, 255, 230))
    return img


if __name__ == "__main__":
    img = render()
    for size in (180, 192, 512):
        img.resize((size, size), Image.LANCZOS).save(
            os.path.join(ROOT, "frontend", "m", f"icon-ledger-{size}.png"))
    print("ok")
