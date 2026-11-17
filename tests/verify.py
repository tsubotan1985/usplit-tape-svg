"""Self-check: read the generated SVG back, sample it, compare with the model.

  python3 -m tests.verify model.stl <face-index> [--offset 0.05]
"""
from __future__ import annotations

import argparse
import math
import re
import sys

from app import geom, svgout

TOK = re.compile(r"([MLAZmlaz])|(-?\d*\.?\d+(?:[eE][-+]?\d+)?)")


def parse_path(d):
    """Return list of subpaths, each a list of (x,y), from M/L/A/Z data."""
    toks = [t for t in TOK.findall(d)]
    vals, cmds = [], []
    i = 0
    cur = None
    subs, pts = [], []
    while i < len(toks):
        c, v = toks[i]
        if c:
            cmds.append(c)
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
            x0, y0 = pts[-1]
            rx, ry, phi, large, sweep, x1, y1 = nums
            pts.extend(_arc_points(x0, y0, rx, ry, phi, large, sweep, x1, y1))
        else:
            raise ValueError("unsupported command " + cur)
    if pts:
        subs.append(pts)
    return subs


def _arc_points(x0, y0, rx, ry, phi, large, sweep, x1, y1, n=64):
    if rx == 0 or ry == 0:
        return [(x1, y1)]
    # endpoint -> centre parameterisation (SVG spec F.6.5)
    ph = math.radians(phi)
    dx2, dy2 = (x0 - x1) / 2.0, (y0 - y1) / 2.0
    x1p = dx2 * math.cos(ph) + dy2 * math.sin(ph)
    y1p = -dx2 * math.sin(ph) + dy2 * math.cos(ph)
    rx, ry = abs(rx), abs(ry)
    lam = x1p ** 2 / rx ** 2 + y1p ** 2 / ry ** 2
    if lam > 1:
        rx *= math.sqrt(lam)
        ry *= math.sqrt(lam)
    num = max(0.0, (rx * rx * ry * ry - rx * rx * y1p * y1p - ry * ry * x1p * x1p))
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
    out = []
    for k in range(1, n + 1):
        t = th1 + dth * k / n
        x = cx + rx * math.cos(t) * math.cos(ph) - ry * math.sin(t) * math.sin(ph)
        y = cy + rx * math.cos(t) * math.sin(ph) + ry * math.sin(t) * math.cos(ph)
        out.append((x, y))
    return out


def dist_to_polyline(p, poly):
    best = 1e30
    n = len(poly)
    for i in range(n):
        a = poly[i]
        b = poly[(i + 1) % n]
        vx, vy = b[0] - a[0], b[1] - a[1]
        wx, wy = p[0] - a[0], p[1] - a[1]
        L2 = vx * vx + vy * vy
        t = 0.0 if L2 == 0 else max(0.0, min(1.0, (wx * vx + wy * vy) / L2))
        qx, qy = a[0] + t * vx, a[1] + t * vy
        d = math.hypot(p[0] - qx, p[1] - qy)
        if d < best:
            best = d
    return best


def build(model, face_sel, offset=0.0, margin=4.0):
    tris = geom.read_stl(model)
    faces = geom.detect_faces(tris)
    face = next((f for i, f in enumerate(faces) if f["id"] == face_sel or str(i) == face_sel), None)
    if not face:
        raise SystemExit("face not found")
    cuts = geom.plan_cuts(face["loops"])
    res = svgout.build(face, cuts, {"offset_mm": offset, "margin_mm": margin}, name="t")
    groups = re.findall(
        r'data-cut-order="(\d+)" data-kind="(\w+)" data-copy="\d+"><path d="([^"]*)"', res["svg"]
    )
    return face, cuts, res, groups


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("model")
    ap.add_argument("face", nargs="?", default="0")
    ap.add_argument("--offset", type=float, default=0.0)
    ap.add_argument("--margin", type=float, default=4.0)
    a = ap.parse_args(argv)

    face, cuts, res, groups = build(a.model, a.face, 0.0, a.margin)
    ok = True
    kinds = [g[1] for g in groups]
    if kinds != [k for k in kinds if k == "hole"] + [k for k in kinds if k != "hole"]:
        print("cut order FAIL: holes are not first")
        ok = False
    print("order:", kinds)

    m = a.margin
    for idx, (order, kind, d) in enumerate(groups):
        src = cuts[idx]
        want = [(p[0] - face["bbox"][0] + m, res["stats"]["h"] - (p[1] - face["bbox"][1] + m)) for p in src["pts"]]
        subs = parse_path(d)
        worst = max(dist_to_polyline(p, want) for s in subs for p in s)
        area_e = abs(sum(_area(s) for s in subs))
        area_w = abs(geom._signed_area(want))
        rel = abs(area_e - area_w) / max(1e-9, area_w)
        size = max(max(p[0] for p in want) - min(p[0] for p in want),
                   max(p[1] for p in want) - min(p[1] for p in want))
        tol = max(0.03, 0.005 * size)
        good = worst <= tol
        ok = ok and good
        print("cut%02d %-5s segs=%d maxdev=%.4fmm (tol %.3f) area_err=%.4f%% %s"
              % (int(order), kind, len(subs), worst, tol, rel * 100, "ok" if good else "FAIL"))

    if abs(a.offset) > 1e-9:
        _, _, res2, groups2 = build(a.model, a.face, a.offset, a.margin)
        dw = res2["stats"]["w"] - res["stats"]["w"]
        dh = res2["stats"]["h"] - res["stats"]["h"]
        good = abs(dw - 2 * a.offset) < 0.02 and abs(dh - 2 * a.offset) < 0.02
        print("offset: sheet grew %.3f x %.3f mm (expect %.3f)" % (dw, dh, 2 * a.offset))
        for i, (o, k, d) in enumerate(groups2):
            if k != "hole":
                continue
            b0 = _bb(parse_path(groups[i][2])[0])
            b1 = _bb(parse_path(d)[0])
            dd = (b0[2] - b0[0]) - (b1[2] - b1[0])
            g2 = abs(dd - 2 * a.offset) < 0.02
            good = good and g2
            print("  hole %d diameter change %.3f mm (expect %.3f) %s"
                  % (i + 1, dd, 2 * a.offset, "ok" if g2 else "FAIL"))
        ok = ok and good

    total = abs(sum(_area(s) for g in groups for s in parse_path(g[2])))
    print("sheet %.3f x %.3f mm / path area %.3f mm2" % (res["stats"]["w"], res["stats"]["h"], total))
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def _bb(pts):
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return [min(xs), min(ys), max(xs), max(ys)]


def _area(pts):
    s = 0.0
    n = len(pts)
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        s += x1 * y2 - x2 * y1
    return s / 2.0


if __name__ == "__main__":
    sys.exit(main())
