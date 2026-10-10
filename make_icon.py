# -*- coding: utf-8 -*-
r"""
从 assets/icon_source.png 生成应用图标文件：

    assets/icon.png   256x256 主图，圆角方块外面是透明的
    assets/icon.ico   多尺寸（256/128/64/48/32/24/16），给 Windows 标题栏 + 任务栏用

跑法：
    python -X utf8 make_icon.py

想换图标：把新图（正方形、图案居中、底色别用白色）覆盖到 assets/icon_source.png，
再跑一次本脚本即可。脚本会自动裁到图案的外接方框、把四周的纯白背景刷成透明。
"""

import os

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

APP_DIR = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(APP_DIR, "assets")
SRC = os.path.join(ASSETS, "icon_source.png")
PNG = os.path.join(ASSETS, "icon.png")
ICO = os.path.join(ASSETS, "icon.ico")

ICO_SIZES = [256, 128, 64, 48, 32, 24, 16]
WHITE_MIN = 240          # 三个通道都 >= 240 才算背景白
PAD_RATIO = 0.02         # 图案四周留白（占边长比例），免得圆角贴边被切
MASTER = 256             # icon.png 的边长


def crop_to_art(im):
    """裁到图案（非白像素）的外接方框。"""
    a = np.asarray(im.convert("RGB")).astype(np.int16)
    ys, xs = np.where((a < WHITE_MIN).any(axis=2))
    if len(xs) == 0:
        return im
    return im.crop((int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1))


def punch_out_background(im):
    """把与画布边缘相连的纯白背景刷成透明（方块里的白色字幕条是封闭的，不受影响）。"""
    rgb = im.convert("RGB")
    a = np.asarray(rgb).astype(np.int16)
    near_white = (a >= WHITE_MIN).all(axis=2)
    mask = Image.fromarray(np.where(near_white, 255, 0).astype(np.uint8), "L")
    # 从四角灌水，只有连通到边缘的白才会被标记
    for corner in ((0, 0), (rgb.width - 1, 0), (0, rgb.height - 1), (rgb.width - 1, rgb.height - 1)):
        if mask.getpixel(corner) == 255:
            ImageDraw.floodfill(mask, corner, 128, thresh=0)
    outside = np.asarray(mask) == 128
    alpha = np.where(outside, 0, 255).astype(np.uint8)
    out = rgb.convert("RGBA")
    out.putalpha(Image.fromarray(alpha))
    return out


def square_with_padding(im, size=MASTER):
    """等比缩放并贴到正方形透明画布中央，四周留一点白边。"""
    pad = int(round(size * PAD_RATIO))
    inner = size - pad * 2
    w, h = im.size
    scale = min(inner / w, inner / h)
    new = im.resize((max(1, int(round(w * scale))), max(1, int(round(h * scale)))), Image.LANCZOS)
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.paste(new, ((size - new.width) // 2, (size - new.height) // 2), new)
    return canvas


def main():
    if not os.path.exists(SRC):
        raise SystemExit(f"找不到源图：{SRC}")
    im = Image.open(SRC).convert("RGBA")
    im = punch_out_background(crop_to_art(im))
    # 边缘稍微羽化一下，缩到 16px 时圆角不会有锯齿
    alpha = im.getchannel("A").filter(ImageFilter.GaussianBlur(0.6))
    im.putalpha(alpha)
    im = square_with_padding(im)
    im.save(PNG)
    im.save(ICO, sizes=[(s, s) for s in ICO_SIZES])
    print(f"已生成 {PNG}（{im.width}x{im.height}）")
    print(f"已生成 {ICO}（尺寸 {ICO_SIZES}，{os.path.getsize(ICO)} 字节）")


if __name__ == "__main__":
    main()
