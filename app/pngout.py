"""Rasterise our own generated SVG to PNG (preview / inspection).

Only the subset of SVG that svgout writes is supported: <g data-kind><path d="M/L/A/Z">.
Arcs are sampled densely, so circles stay round at any dpi.
"""
from __future__ import annotations

import math
import re

try:
    from PIL import Image, ImageDraw
except ImportError:  # サーバ用途では不要（CLIのみ）
    Image = ImageDraw = None

TOK = re.compile(r"([MLAZmlaz])|(-?\d*\.?\d+(?:[eE][-+]?\d+)?)")
PATH_RE = re.compile(
    r'data-kind="(\w+)" data-copy="\d+"><path d="([^"]*)"'
)


def _arc_points(x0, y0, rx, ry, phi, large, sweep, x1, y1, n=256):
    if rx == 0 or ry == 0:
        return [(x1, y1)]
    ph = math.radians(phi)
    dx2, dy2 = (x0 - x1) / 2.0, (y0 - y1) / 2.0
    x1p = dx2 * math.cos(ph) + dy2 * math.sin(ph)
    y1p = -dx2 * math.sin(ph) + dy2 * math.cos(ph)
    rx, ry = abs(rx), abs(ry)
    lam = x1p ** 2 / rx ** 2 + y1p ** 2 / ry ** 2
    if lam > 1:
        rx *= math.sqrt(lam)
        ry *= math.sqrt(lam)
    num = max(0.0, rx * rx * ry * ry - rx * rx * y1p * y1p - ry * ry * x1p * x1p)
    den = rx * rx * y1p * y1p + ry * ry * x1p * x1p
    co = math.sqrt(num / den) if den else 0.0
    if large == sweep:
        co = -co
    cxp = co * rx * y1p / ry
    cyp = -co * ry * x1p / rx
    cx = cxp * math.cos(ph) - cyp * math.sin(ph) + (x0 + x1) / 2
    cy = cxp * math.sin(ph) + cyp * math.cos(ph) + (y0 + y1) / 2
    th1 = math.atan2((y1p - cyp) / ry, (x1p - cxp) / rx)
    th2 = math.atan2((-y1p - cyp) / ry, (-x1p - cxp) / rx)
    dth = th2 - th1
    if not sweep and dth > 0:
        dth -= 2 * math.pi
    elif sweep and dth < 0:
        dth += 2 * math.pi
    return [
        (
            cx + rx * math.cos(th1 + dth * k / n) * math.cos(ph) - ry * math.sin(th1 + dth * k / n) * math.sin(ph),
            cy + rx * math.cos(th1 + dth * k / n) * math.sin(ph) + ry * math.sin(th1 + dth * k / n) * math.cos(ph),
        )
        for k in range(1, n + 1)
    ]


def parse_path(d):
    toks = TOK.findall(d)
    i = 0
    cur = None
    subs, pts = [], []
    while i < len(toks):
        c, _v = toks[i]
        if c:
            cur = c
            i += 1
            continue
        if cur is None:
            raise ValueError("path must start with a command")
        n = 7 if cur in "Aa" else 2
        nums = [float(toks[i + k][1]) for k in range(n)]
        i += n
        if cur in "Mm":
            if pts:
                subs.append(pts)
            pts = [(nums[0], nums[1])]
            cur = "L" if cur == "m" else "M"
        elif cur in "Ll":
            pts.append((nums[0], nums[1]))
        elif cur in "Aa":
            pts.extend(_arc_points(pts[-1][0], pts[-1][1], *nums))
        else:
            raise ValueError("unsupported command " + cur)
    if pts:
        subs.append(pts)
    return subs


def svg_to_png(svg, out_path, dpi=400, stroke_mm=0.18, supersample=3, invert=False):
    if Image is None:
        raise SystemExit("PNG書き出しには Pillow が必要です: pip install pillow")
    m = re.search(r'width="([\d.]+)mm" height="([\d.]+)mm"', svg)
    if not m:
        raise ValueError("width/height(mm) がSVGにありません")
    w_mm, h_mm = float(m.group(1)), float(m.group(2))
    k = dpi / 25.4
    s = max(1, int(supersample))
    W, H = int(round(w_mm * k)), int(round(h_mm * k))
    canvas = Image.new("L", (W * s, H * s), 0 if invert else 255)
    draw = ImageDraw.Draw(canvas)
    ink = 255 if invert else 0
    width = max(1, int(round(stroke_mm * k * s)))
    for kind, d in PATH_RE.findall(svg):
        for sub in parse_path(d):
            if len(sub) < 2:
                continue
            xy = [(p[0] * k * s, p[1] * k * s) for p in sub]
            draw.line(xy + [xy[0]], fill=ink, width=width, joint="curve")
            if width > 2:  # round the ends
                r = width / 2.0
                for p in (xy[0], xy[-1]):
                    draw.ellipse([p[0] - r, p[1] - r, p[0] + r, p[1] + r], fill=ink)
    img = canvas.resize((W, H), Image.LANCZOS)
    if invert:
        img = img.point(lambda v: 255 - v)
    img.save(out_path, "PNG", dpi=(dpi, dpi))
    return {"png": out_path, "w": W, "h": H, "dpi": dpi}
