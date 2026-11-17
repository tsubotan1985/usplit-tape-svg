"""Contours -> SVG for cutting machines (mm, 1:1).

The DOM order of the <g id="cutNN"> groups IS the cut order: cutting
software that honours z-order / first-drawn-first-cut will cut the holes
before the outline.  `data-cut-order` is there for tools that expose it.
"""
from __future__ import annotations

import math

from . import geom

HEADER = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    "<!-- uSPLIT Tape SVG / cut order = document order of groups cutNN -->\n"
)


def _fmt(v):
    s = "%.4f" % v
    s = s.rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


# ------------------------------------------------------------------ geometry


def _apply(pts, opts, is_hole):
    d = float(opts.get("offset_mm", 0.0) or 0.0)
    if abs(d) > 1e-9:
        # positive offset = the piece gets bigger (outline out, holes in)
        outward = (d > 0) != is_hole
        pts = geom.offset_loop(pts, abs(d), outward=outward)
    if opts.get("mirror_x"):
        pts = [(-p[0], p[1]) for p in pts]
    if opts.get("mirror_y"):
        pts = [(p[0], -p[1]) for p in pts]
    rot = int(opts.get("rotate", 0) or 0) % 360
    if rot:
        c, s = math.cos(math.radians(rot)), math.sin(math.radians(rot))
        pts = [(p[0] * c - p[1] * s, p[0] * s + p[1] * c) for p in pts]
    return pts


def _split_tabs(pts, n_tabs, tab_len):
    """Split a closed contour into open runs, leaving n_tabs bridges."""
    n = len(pts)
    if n_tabs <= 0 or n < 10:
        return [pts], True
    cum = [0.0]
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        cum.append(cum[-1] + math.hypot(x2 - x1, y2 - y1))
    total = cum[-1]
    if total <= 0:
        return [pts], True
    tab_len = min(tab_len, total / (n_tabs + 1) * 0.8)
    bands = []
    for k in range(n_tabs):
        s = total * (k + 0.5) / n_tabs
        a = (s - tab_len / 2) % total
        b = (s + tab_len / 2) % total
        bands.append((a, b))
    keep = []
    for i in range(n):
        s = (cum[i] + cum[i + 1]) / 2
        inband = False
        for a, b in bands:
            if a <= b:
                inband = inband or (a <= s <= b)
            else:
                inband = inband or (s >= a or s <= b)
        keep.append(not inband)
    runs, cur = [], []
    for i in range(n):
        if keep[i]:
            cur.append(pts[i])
        else:
            if len(cur) >= 2:
                runs.append(cur)
            cur = []
    if len(cur) >= 2:
        runs.append(cur)
    if len(runs) > 1 and keep[0] and keep[-1]:
        runs[0] = runs[-1] + runs[0]
        runs.pop()
    if len(runs) <= 1:
        return [pts], True
    return runs, False


def _d_closed(pts, arc_fit):
    pts = pts + [pts[0]]
    c = geom.circle_of(pts[:-1])
    if c and arc_fit:
        cx, cy, r = c
        ccw = geom._signed_area(pts) > 0  # in the emitted (y-down) frame
        sweep = 1 if ccw else 0
        return "M %s %s A %s %s 0 1 %d %s %s A %s %s 0 1 %d %s %s Z" % (
            _fmt(cx - r), _fmt(cy), _fmt(r), _fmt(r), sweep, _fmt(cx + r), _fmt(cy),
            _fmt(r), _fmt(r), sweep, _fmt(cx - r), _fmt(cy),
        )
    return _d_open(pts, arc_fit) + " Z"


def _d_open(pts, arc_fit):
    segs = geom.path_segments(pts, allow_arcs=arc_fit)
    out = ["M %s %s" % (_fmt(pts[0][0]), _fmt(pts[0][1]))]
    for s in segs:
        if s[0] == "L":
            out.append("L %s %s" % (_fmt(s[1][0]), _fmt(s[1][1])))
        else:
            _, r, large, sweep, p = s
            out.append("A %s %s 0 %d %d %s %s" % (_fmt(r), _fmt(r), large, sweep, _fmt(p[0]), _fmt(p[1])))
    return " ".join(out)


def _bbox(pts):
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return [min(xs), min(ys), max(xs), max(ys)]


# -------------------------------------------------------------------- build


def build(face, cuts, opts, name="uSPLIT", title_extra=""):
    margin = max(0.0, float(opts.get("margin_mm", 4.0) or 0.0))
    gap = max(0.0, float(opts.get("copy_gap_mm", 3.0) or 0.0))
    copies = max(1, min(30, int(opts.get("copies", 1) or 1)))
    arc_fit = bool(opts.get("arc_fit", True))
    n_tabs = max(0, min(8, int(opts.get("tabs", 0) or 0)))
    tab_len = max(0.2, float(opts.get("tab_len_mm", 1.2) or 1.2))

    placed = [
        dict(c, pts=_apply(c["pts"], opts, c["kind"] == "hole"))
        for c in cuts
    ]
    b = _bbox([p for c in placed for p in c["pts"]])
    w, h = b[2] - b[0], b[3] - b[1]

    # repeat copies along the longer side (tape rolls are long)
    along_x = w >= h
    step_x = (w + gap) if along_x else 0.0
    step_y = 0.0 if along_x else (h + gap)

    all_pts, layers = [], []
    for k in range(copies):
        dx, dy = step_x * k, step_y * k
        cop = []
        for c in placed:
            q = [(p[0] - b[0] + dx, p[1] - b[1] + dy) for p in c["pts"]]
            cop.append(dict(c, pts=q))
            all_pts.extend(q)
        layers.append(cop)

    tb = _bbox(all_pts)
    W = tb[2] - tb[0] + 2 * margin
    H = tb[3] - tb[1] + 2 * margin

    def place(p):
        # math frame -> SVG frame (y down), with the margin applied
        return (p[0] - tb[0] + margin, H - (p[1] - tb[1] + margin))

    parts, rows = [], []
    k = 0
    for ci, cop in enumerate(layers):
        for c in cop:
            fp = [place(p) for p in c["pts"]]
            if c["kind"] == "outer":
                runs, closed = _split_tabs(fp, n_tabs, tab_len)
            else:
                runs, closed = [fp], True
            ds = []
            for rp in runs:
                ds.append(_d_closed(rp, arc_fit) if closed else _d_open(rp, arc_fit))
            d = " ".join(ds)
            k += 1
            parts.append(
                '<g id="cut%02d" data-cut-order="%d" data-kind="%s" '
                'data-copy="%d"><path d="%s"/></g>'
                % (k, k, c["kind"], (k - 1) // max(1, len(placed)) + 1, d)
            )
            row = {
                "n": k,
                "kind": c["kind"],
                "segments": len(runs),
                "length": round(sum(_run_len(r) for r in runs), 3),
                "cx": round(sum(p[0] for p in fp) / len(fp), 2),
                "cy": round(sum(p[1] for p in fp) / len(fp), 2),
                "sx": round(fp[0][0], 2),
                "sy": round(fp[0][1], 2),
                "size": round(max(_bbox(fp)[2] - _bbox(fp)[0], _bbox(fp)[3] - _bbox(fp)[1]), 2),
                "id": c.get("cid"),
                "start_index": c.get("start_index"),
                "dir": c.get("dir"),
            }
            if ci == 0 and c.get("pts0"):
                p0 = _apply(c["pts0"], opts, c["kind"] == "hole")
                row["pts"] = [
                    [round(place((p[0] - b[0], p[1] - b[1]))[0], 2),
                     round(place((p[0] - b[0], p[1] - b[1]))[1], 2)]
                    for p in p0
                ]
            rows.append(row)

    guides = _guides(W, H, margin) if opts.get("guides") else ""
    svg = "".join(
        [
            HEADER,
            '<svg xmlns="http://www.w3.org/2000/svg" version="1.1" '
            'width="%smm" height="%smm" viewBox="0 0 %s %s">' % (_fmt(W), _fmt(H), _fmt(W), _fmt(H)),
            "<title>%s</title>" % _esc(name + title_extra),
            "<desc>%s</desc>" % _esc(_desc(face, rows)),
            '<g id="CUT" fill="none" stroke="#111111" stroke-width="0.15" '
            'stroke-linecap="round" stroke-linejoin="round">',
            "".join(parts),
            "</g>",
            guides,
            "</svg>",
        ]
    )
    stats = {
        "w": round(W, 3),
        "h": round(H, 3),
        "piece_w": round(w, 3),
        "piece_h": round(h, 3),
        "copies": copies,
        "n_cuts": k,
        "cut_length": round(sum(r["length"] for r in rows), 2),
        "order": rows,
    }
    return {"svg": svg, "stats": stats}


def _run_len(pts):
    return sum(math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1]) for i in range(len(pts) - 1))


def _guides(w, h, m):
    y = h - max(1.2, m * 0.45)
    x0 = max(m, 2.0)
    return (
        '<g id="GUIDES" data-do-not-cut="1" style="display:none" fill="none" '
        'stroke="#b4452f" stroke-width="0.2"><path d="M %s %s L %s %s M %s %s L %s %s M %s %s L %s %s"/></g>'
        % (
            _fmt(x0), _fmt(y), _fmt(x0 + 10), _fmt(y),
            _fmt(x0), _fmt(y - 1.5), _fmt(x0), _fmt(y + 1.5),
            _fmt(x0 + 10), _fmt(y - 1.5), _fmt(x0 + 10), _fmt(y + 1.5),
        )
    )


def _esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _desc(face, rows):
    return "%s / 面 %s offset %.2fmm / カット順 %s" % (
        "uSPLIT tape",
        face.get("axis", ""),
        face.get("offset", 0.0),
        ", ".join("%d:%s" % (r["n"], r["kind"]) for r in rows),
    )


# ---------------------------------------------------------------- thumbnail


def thumbnail(face, size=110):
    b = face["bbox"]
    w = max(1e-6, b[2] - b[0])
    h = max(1e-6, b[3] - b[1])
    pad = 0.08 * max(w, h)
    vb = "%s %s %s %s" % (_fmt(b[0] - pad), _fmt(-(b[3] + pad)), _fmt(w + 2 * pad), _fmt(h + 2 * pad))
    paths = []
    for l in face["loops"]:
        d = _d_closed([(p[0], -p[1]) for p in l["pts"]], True)
        paths.append('<path d="%s"/>' % d)
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="%s" width="%d" height="%d" '
        'preserveAspectRatio="xMidYMid meet" fill="none" stroke="#16202c" '
        'stroke-width="%s">%s</svg>'
        % (vb, size, size, _fmt(max(w, h) / 70.0), "".join(paths))
    )
