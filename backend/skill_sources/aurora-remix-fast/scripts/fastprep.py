#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""fastprep.py - 对标二创·快版 的备料 / 快检小工具。

子命令:
  ratio   <image>
      读尺寸, 输出建议画布 (builtin 路由 / gateway 路由各一条)。

  crop    <image> --out <dir> --spec "x0,y0,x1,y1;x0,y0,x1,y1;..."
      按归一化框把一张长图切成多屏, 输出 U01.png U02.png ...

  board   --out <path> <img> [img ...] [--axis v|h] [--gap 6] [--width 1200]
      把多张产品原图拼成一张形态板 (统一朝向、统一宽度)。

  wmcheck <image> [--out <path>] [--frac 0.30 0.12] [--scale 2]
      裁右下角一块并放大, 用于肉眼查水印。

  zoom    <image> --box x0,y0,x1,y1 --out <path> [--scale 3]
      按归一化框局部放大, 用于核对关键特征 (条纹方向 / 腰头 / 底裆 ...)。

约定: 所有坐标一律归一化 0..1 (相对宽 / 高), 与分辨率无关。
"""
import argparse
import json
import os
import sys

from PIL import Image

GATEWAY_SIZES = {
    "1:1": (1536, 1536),
    "3:2": (1536, 1024),
    "2:3": (1024, 1536),
    "16:9": (1536, 864),
    "3:4": (1152, 1536),
}
BUILTIN_TARGET_AREA = 1024 * 1360  # 内置路由的甜点分辨率 (~1.4MP)


def _die(msg, code=2):
    print(json.dumps({"error": msg}, ensure_ascii=False))
    sys.exit(code)


def _parse_boxes(spec):
    boxes = []
    for i, chunk in enumerate(spec.split(";")):
        chunk = chunk.strip()
        if not chunk:
            continue
        parts = [p.strip() for p in chunk.split(",")]
        if len(parts) != 4:
            _die("spec 第 %d 段不是 x0,y0,x1,y1: %r" % (i + 1, chunk))
        try:
            x0, y0, x1, y1 = [float(p) for p in parts]
        except ValueError:
            _die("spec 第 %d 段含非数字: %r" % (i + 1, chunk))
        if not (0.0 <= x0 < x1 <= 1.0 and 0.0 <= y0 < y1 <= 1.0):
            _die("spec 第 %d 段越界或顺序反了 (要求 0<=x0<x1<=1, 0<=y0<y1<=1): %r" % (i + 1, chunk))
        boxes.append((x0, y0, x1, y1))
    if not boxes:
        _die("spec 为空")
    return boxes


def _nearest_label(r):
    cands = [("1:1", 1.0), ("3:2", 1.5), ("4:3", 4.0 / 3), ("2:3", 2.0 / 3),
             ("3:4", 0.75), ("16:9", 16.0 / 9), ("9:16", 9.0 / 16)]
    k, v = min(cands, key=lambda kv: abs(kv[1] - r))
    if abs(v - r) / r <= 0.01:
        return k
    return "%s(最近)" % k


def _best_builtin_size(r):
    """在 16 整除 + 比例偏差 <=1% 的候选里, 取最接近甜点面积的。"""
    best = None
    for w in range(512, 2065, 16):
        h = int(round((w / r) / 16.0)) * 16
        if h <= 0:
            continue
        delta = abs((w / float(h)) - r) / r
        if delta > 0.01:
            continue
        score = abs(w * h - BUILTIN_TARGET_AREA)
        if best is None or score < best[0]:
            best = (score, w, h, delta)
    if best is None:
        return None
    _, w, h, delta = best
    return {"size": "%dx%d" % (w, h), "aspect_delta": round(delta, 6),
            "divisible_by_16": True}


def cmd_ratio(a):
    with Image.open(a.image) as im:
        w, h = im.size
    r = w / float(h)
    gk = min(GATEWAY_SIZES, key=lambda k: abs(GATEWAY_SIZES[k][0] / float(GATEWAY_SIZES[k][1]) - r))
    gw, gh = GATEWAY_SIZES[gk]
    out = {
        "image": os.path.abspath(a.image),
        "width": w,
        "height": h,
        "ratio": round(r, 6),
        "ratio_label": _nearest_label(r),
        "builtin_size": _best_builtin_size(r),
        "gateway": {
            "aspect": gk,
            "size": "%dx%d" % (gw, gh),
            "aspect_delta": round(abs(gw / float(gh) - r) / r, 6),
        },
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))


def cmd_crop(a):
    boxes = _parse_boxes(a.spec)
    os.makedirs(a.out, exist_ok=True)
    with Image.open(a.image) as im:
        im = im.convert("RGBA")
        W, H = im.size
        made = []
        for i, (x0, y0, x1, y1) in enumerate(boxes, start=1):
            px = (int(round(x0 * W)), int(round(y0 * H)),
                  int(round(x1 * W)), int(round(y1 * H)))
            tile = im.crop(px)
            name = "%s%02d.png" % (a.prefix, i)
            path = os.path.join(a.out, name)
            tile.save(path, "PNG")
            made.append({"name": name, "path": os.path.abspath(path),
                         "box_norm": [x0, y0, x1, y1], "box_px": list(px),
                         "width": tile.width, "height": tile.height,
                         "ratio": round(tile.width / float(tile.height), 6)})
    print(json.dumps({"source": os.path.abspath(a.image), "count": len(made),
                      "units": made}, ensure_ascii=False, indent=2))


def cmd_board(a):
    paths = [p for p in a.images if p]
    if not paths:
        _die("至少要给一张图")
    for p in paths:
        if not os.path.isfile(p):
            _die("找不到图: %s" % p)
    imgs = []
    for p in paths:
        im = Image.open(p).convert("RGB")
        if im.width != a.width:
            nh = max(1, int(round(im.height * a.width / float(im.width))))
            im = im.resize((a.width, nh), Image.LANCZOS)
        imgs.append(im)
    gap = max(0, a.gap)
    if a.axis == "v":
        total = sum(i.height for i in imgs) + gap * (len(imgs) - 1)
        canvas = Image.new("RGB", (a.width, total), (255, 255, 255))
        y = 0
        for im in imgs:
            canvas.paste(im, (0, y))
            y += im.height + gap
    else:
        total = sum(i.width for i in imgs) + gap * (len(imgs) - 1)
        hmax = max(i.height for i in imgs)
        canvas = Image.new("RGB", (total, hmax), (255, 255, 255))
        x = 0
        for im in imgs:
            canvas.paste(im, (x, 0))
            x += im.width + gap
    outdir = os.path.dirname(os.path.abspath(a.out))
    if outdir:
        os.makedirs(outdir, exist_ok=True)
    canvas.save(a.out, "PNG")
    print(json.dumps({"out": os.path.abspath(a.out), "axis": a.axis,
                      "panel_width": a.width, "panels": len(imgs),
                      "width": canvas.width, "height": canvas.height},
                     ensure_ascii=False, indent=2))


def cmd_wmcheck(a):
    fw, fh = a.frac
    with Image.open(a.image) as im:
        im = im.convert("RGB")
        W, H = im.size
        x0 = int(round((1.0 - fw) * W))
        y0 = int(round((1.0 - fh) * H))
        box = (x0, y0, W, H)
        tile = im.crop(box)
        tile = tile.resize((tile.width * a.scale, tile.height * a.scale), Image.LANCZOS)
        out = a.out or (a.image + ".wmcheck.png")
        tile.save(out, "PNG")
    print(json.dumps({"out": os.path.abspath(out), "source": os.path.abspath(a.image),
                      "crop_box_px": list(box), "crop_frac": [fw, fh],
                      "scale": a.scale, "width": tile.width, "height": tile.height},
                     ensure_ascii=False, indent=2))


def cmd_zoom(a):
    box = _parse_boxes(a.box)[0]
    with Image.open(a.image) as im:
        im = im.convert("RGB")
        W, H = im.size
        px = (int(round(box[0] * W)), int(round(box[1] * H)),
              int(round(box[2] * W)), int(round(box[3] * H)))
        tile = im.crop(px)
        tile = tile.resize((max(1, tile.width * a.scale), max(1, tile.height * a.scale)),
                           Image.LANCZOS)
        tile.save(a.out, "PNG")
    print(json.dumps({"out": os.path.abspath(a.out), "source": os.path.abspath(a.image),
                      "box_norm": list(box), "box_px": list(px), "scale": a.scale,
                      "width": tile.width, "height": tile.height},
                     ensure_ascii=False, indent=2))


def main():
    ap = argparse.ArgumentParser(description="对标二创·快版 备料/快检工具")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("ratio", help="读尺寸 + 建议画布")
    p.add_argument("image")
    p.set_defaults(func=cmd_ratio)

    p = sub.add_parser("crop", help="按归一化框切屏")
    p.add_argument("image")
    p.add_argument("--out", required=True)
    p.add_argument("--spec", required=True, help='"x0,y0,x1,y1;..." 归一化')
    p.add_argument("--prefix", default="U")
    p.set_defaults(func=cmd_crop)

    p = sub.add_parser("board", help="拼形态板")
    p.add_argument("images", nargs="+")
    p.add_argument("--out", required=True)
    p.add_argument("--axis", choices=["v", "h"], default="v")
    p.add_argument("--gap", type=int, default=6)
    p.add_argument("--width", type=int, default=1200)
    p.set_defaults(func=cmd_board)

    p = sub.add_parser("wmcheck", help="裁右下角放大查水印")
    p.add_argument("image")
    p.add_argument("--out", default=None)
    p.add_argument("--frac", nargs=2, type=float, default=[0.30, 0.12])
    p.add_argument("--scale", type=int, default=2)
    p.set_defaults(func=cmd_wmcheck)

    p = sub.add_parser("zoom", help="局部放大核对特征")
    p.add_argument("image")
    p.add_argument("--box", required=True, help='"x0,y0,x1,y1" 归一化')
    p.add_argument("--out", required=True)
    p.add_argument("--scale", type=int, default=3)
    p.set_defaults(func=cmd_zoom)

    a = ap.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()
