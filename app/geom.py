"""STL -> planar faces -> contours -> cut order.

Pure standard library (no numpy / no trimesh) so the container stays tiny.
All geometry is done in model coordinates (mm, y up).  The SVG writer flips
y at emission time.
"""
from __future__ import annotations

import math
import struct
from collections import defaultdict

# ---------------------------------------------------------------- STL reading


def read_stl(path):
    """Return list of triangles, each ((x,y,z),(x,y,z),(x,y,z))."""
    with open(path, "rb") as f:
        data = f.read()
    if len(data) >= 84:
        n = struct.unpack("<I", data[80:84])[0]
        if 84 + 50 * n == len(data):
            tris = []
            for rec in struct.iter_unpack("<12fH", data[84:]):
                tris.append(
                    (
                        (rec[3], rec[4], rec[5]),
                        (rec[6], rec[7], rec[8]),
                        (rec[9], rec[10], rec[11]),
                    )
                )
            return tris
    return _read_ascii_stl(data)


def _read_ascii_stl(data):
    tris, cur = [], []
    for line in data.decode("utf-8", "replace").splitlines():
        s = line.strip()
        if s.startswith("vertex "):
            p = s.split()
            cur.append((float(p[1]), float(p[2]), float(p[3])))
            if len(cur) == 3:
                tris.append(tuple(cur))
                cur = []
    if not tris:
        raise ValueError("STLとして読めませんでした (binary/ascii どちらでもない)")
    return tris


# ------------------------------------------------------------- small helpers


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _cross(a, b):
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _norm(a):
    return math.sqrt(_dot(a, a))


def _unit(a):
    n = _norm(a)
    if n < 1e-12:
        return None
    return (a[0] / n, a[1] / n, a[2] / n)


def _tri_area_normal(t):
    n = _cross(_sub(t[1], t[0]), _sub(t[2], t[0]))
    ln = _norm(n)
    if ln < 1e-12:
        return 0.0, None
    return ln / 2.0, (n[0] / ln, n[1] / ln, n[2] / ln)


# ------------------------------------------------------------ planar patches

AXIS_NAME = {0: "X", 1: "Y", 2: "Z"}


def _face_key(n, d):
    return (
        round(n[0], 3),
        round(n[1], 3),
        round(n[2], 3),
        round(d, 2),
    )


def detect_faces(tris, min_area=0.8):
    """Group coplanar triangles into 2D patches with holes.

    Returns a list of dicts (loops included, so callers can render).
    """
    groups = defaultdict(lambda: {"area": 0.0, "tris": []})
    for idx, t in enumerate(tris):
        a, n = _tri_area_normal(t)
        if not n:
            continue
        d = _dot(n, t[0])
        g = groups[_face_key(n, d)]
        g["area"] += a
        g["tris"].append(idx)
        g["n"] = (_face_key(n, d)[0], _face_key(n, d)[1], _face_key(n, d)[2])
        g["d"] = _face_key(n, d)[3]

    faces = []
    for key, g in groups.items():
        if g["area"] < min_area:
            continue
        n = g["n"]
        if _norm(n) < 0.5:  # rounding collapsed a normal, ignore
            continue
        u, v = _basis(n)
        loops = _patch_loops([tris[i] for i in g["tris"]], n, u, v)
        if not loops:
            continue
        _classify_loops(loops)
        axis = _axis_label(n)
        faces.append(
            {
                "id": _stable_id(key),
                "key": key,
                "normal": list(n),
                "offset": g["d"],
                "axis": axis,
                "area": round(g["area"], 3),
                "loops": loops,
                "bbox": _loops_bbox(loops),
                "n_outer": sum(1 for l in loops if l["depth"] % 2 == 0),
                "n_holes": sum(1 for l in loops if l["depth"] % 2 == 1),
            }
        )
    faces.sort(key=lambda f: (f["n_holes"] > 0, f["area"]), reverse=True)
    return faces


def _stable_id(key):
    import hashlib

    s = "%.3f_%.3f_%.3f_%.2f" % key
    return hashlib.md5(s.encode()).hexdigest()[:10]


def _axis_label(n):
    for i in range(3):
        if abs(abs(n[i]) - 1.0) < 1e-3:
            return AXIS_NAME[i] + ("+" if n[i] > 0 else "-")
    return "N(%.2f,%.2f,%.2f)" % n


def _basis(n):
    """Right-handed 2D frame (u, v, n) in the natural model axes."""
    for i in range(3):
        if abs(abs(n[i]) - 1.0) < 1e-6:
            ax = [0.0, 0.0, 0.0]
            ax[(i + 1) % 3] = 1.0
            u = (ax[0], ax[1], ax[2])
            v = _unit(_cross(n, u))
            return u, v
    ref = (0.0, 0.0, 1.0) if abs(n[2]) < 0.9 else (1.0, 0.0, 0.0)
    u = _unit(_cross(ref, n))
    if u is None:
        u = (1.0, 0.0, 0.0)
    v = _unit(_cross(n, u))
    return u, v


def _patch_loops(tri_list, n, u, v):
    """Boundary edges of a coplanar triangle patch -> closed loops (2D)."""
    key2pt = {}
    idx_of = []

    def vid(p):
        k = (round(p[0], 3), round(p[1], 3), round(p[2], 3))
        i = key2pt.get(k)
        if i is None:
            i = len(key2pt)
            key2pt[k] = i
        return i

    edges = defaultdict(int)
    for t in tri_list:
        a, b, c = vid(t[0]), vid(t[1]), vid(t[2])
        for e in ((a, b), (b, c), (c, a)):
            edges[(min(e), max(e))] += 1

    adj = defaultdict(list)
    nb = 0
    for (a, b), c in edges.items():
        if c == 1:
            adj[a].append(b)
            adj[b].append(a)
            nb += 1
    if nb == 0:
        return []

    pts = [None] * len(key2pt)
    for k, i in key2pt.items():
        pts[i] = (_dot(k, u), _dot(k, v))

    used = set()
    loops = []
    for a, b in ((e[0], e[1]) for e in edges if edges[e] == 1):
        e0 = (min(a, b), max(a, b))
        if e0 in used:
            continue
        seq = [a]
        used.add(e0)
        cur = b
        guard = 0
        while cur != seq[0] and guard < 200000:
            guard += 1
            seq.append(cur)
            nxt = None
            for w in adj[cur]:
                k = (min(cur, w), max(cur, w))
                if k not in used:
                    nxt = w
                    break
            if nxt is None:
                break
            used.add((min(cur, nxt), max(cur, nxt)))
            cur = nxt
        if len(seq) >= 3:
            loops.append(_make_loop([pts[i] for i in seq]))
    return loops


def _make_loop(xy):
    xy = _dedupe(xy)
    a = _signed_area(xy)
    return {
        "pts": xy,
        "area": abs(a),
        "signed": a,
        "bbox": _pts_bbox(xy),
        "depth": 0,
        "circle": _as_circle(xy),
    }


def _dedupe(xy, tol=1e-6):
    out = []
    for p in xy:
        if not out or abs(p[0] - out[-1][0]) > tol or abs(p[1] - out[-1][1]) > tol:
            out.append(p)
    if len(out) > 1 and abs(out[0][0] - out[-1][0]) < tol and abs(out[0][1] - out[-1][1]) < tol:
        out.pop()
    return out


def _signed_area(xy):
    s = 0.0
    n = len(xy)
    for i in range(n):
        x1, y1 = xy[i]
        x2, y2 = xy[(i + 1) % n]
        s += x1 * y2 - x2 * y1
    return s / 2.0


def _pts_bbox(xy):
    xs = [p[0] for p in xy]
    ys = [p[1] for p in xy]
    return [min(xs), min(ys), max(xs), max(ys)]


def _loops_bbox(loops):
    outers = [l for l in loops if l["depth"] % 2 == 0] or loops
    b = [1e30, 1e30, -1e30, -1e30]
    for l in outers:
        b[0] = min(b[0], l["bbox"][0])
        b[1] = min(b[1], l["bbox"][1])
        b[2] = max(b[2], l["bbox"][2])
        b[3] = max(b[3], l["bbox"][3])
    return [round(v, 3) for v in b]


def _as_circle(xy):
    """If the loop is a circle within tolerance, return its params."""
    if len(xy) < 8:
        return None
    cx = sum(p[0] for p in xy) / len(xy)
    cy = sum(p[1] for p in xy) / len(xy)
    rs = [math.hypot(p[0] - cx, p[1] - cy) for p in xy]
    rmean = sum(rs) / len(rs)
    if rmean < 0.2:
        return None
    if max(abs(r - rmean) for r in rs) > max(0.01, 0.01 * rmean):
        return None
    return {"cx": cx, "cy": cy, "r": rmean, "d": 2 * rmean}


def _classify_loops(loops):
    for i, li in enumerate(loops):
        probe = li["pts"][0]
        depth = 0
        for j, lj in enumerate(loops):
            if i == j or lj["area"] <= li["area"]:
                continue
            if _point_in_poly(probe, lj["pts"]):
                depth += 1
        li["depth"] = depth


def _point_in_poly(pt, poly):
    x, y = pt
    inside = False
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if (y1 > y) != (y2 > y):
            t = (y - y1) / (y2 - y1)
            if x < x1 + t * (x2 - x1):
                inside = not inside
    return inside


# ------------------------------------------------------- contour preparation


def _reverse(xy):
    return list(reversed(xy))


def offset_loop(xy, dist, outward=True):
    """Mitre-offset a closed loop.  dist>0 grows the loop when outward."""
    if abs(dist) < 1e-9:
        return list(xy)
    sgn = 1.0 if outward else -1.0
    # normalise to CCW so that (dy,-dx) points outward
    ccw = _signed_area(xy) > 0
    pts = list(xy) if ccw else _reverse(xy)
    n = len(pts)
    out = []
    for i in range(n):
        p0 = pts[(i - 1) % n]
        p1 = pts[i]
        p2 = pts[(i + 1) % n]
        e1 = (p1[0] - p0[0], p1[1] - p0[1])
        e2 = (p2[0] - p1[0], p2[1] - p1[1])
        l1 = math.hypot(*e1) or 1e-12
        l2 = math.hypot(*e2) or 1e-12
        n1 = (e1[1] / l1, -e1[0] / l1)
        n2 = (e2[1] / l2, -e2[0] / l2)
        bis = (n1[0] + n2[0], n1[1] + n2[1])
        lb = math.hypot(*bis)
        if lb < 1e-9:
            bis = n1
            lb = 1.0
        bis = (bis[0] / lb, bis[1] / lb)
        cosh = bis[0] * n1[0] + bis[1] * n1[1]
        if abs(cosh) < 0.2:  # very sharp corner -> clamp the mitre
            scale = dist * sgn * 4.0
        else:
            scale = dist * sgn / cosh
            scale = max(-4.0 * abs(dist), min(4.0 * abs(dist), scale))
        out.append((p1[0] + bis[0] * scale, p1[1] + bis[1] * scale))
    return out


def rotate_to_nearest(xy, target):
    """Rotate the point list so it starts at the vertex nearest `target`."""
    if not target or len(xy) < 2:
        return list(xy)
    best, bi = 1e30, 0
    for i, p in enumerate(xy):
        d = (p[0] - target[0]) ** 2 + (p[1] - target[1]) ** 2
        if d < best:
            best, bi = d, i
    return xy[bi:] + xy[:bi]


def best_start_index(xy):
    """Prefer a corner (largest turn) so the entry point is a real corner."""
    n = len(xy)
    if n < 3:
        return 0
    best, bi = -1.0, 0
    for i in range(n):
        p0, p1, p2 = xy[i - 1], xy[i], xy[(i + 1) % n]
        a = (p0[0] - p1[0], p0[1] - p1[1])
        b = (p2[0] - p1[0], p2[1] - p1[1])
        la, lb = math.hypot(*a), math.hypot(*b)
        if la < 1e-9 or lb < 1e-9:
            continue
        c = (a[0] * b[0] + a[1] * b[1]) / (la * lb)
        c = max(-1.0, min(1.0, c))
        ang = math.acos(c)
        if ang > best:
            best, bi = ang, i
    return bi


def piece_table(loops):
    """Stable ids for the separate outer pieces of one face.

    Ids are assigned by descending area, so they do not move when cut order or
    export options change.
    """
    outers = [l for l in loops if l["depth"] % 2 == 0]
    order = sorted(outers, key=lambda l: (-l["area"], l["bbox"][0], l["bbox"][1]))
    table = []
    for i, l in enumerate(order):
        table.append(
            {
                "id": str(i + 1),
                "loop": l,
                "area": round(l["area"], 2),
                "w": round(l["bbox"][2] - l["bbox"][0], 2),
                "h": round(l["bbox"][3] - l["bbox"][1], 2),
                "holes": sum(
                    1
                    for h in loops
                    if h["depth"] % 2 == 1 and _point_in_poly(h["pts"][0], l["pts"])
                ),
            }
        )
    return table


def contour_table(loops):
    """Every contour of a face with a stable id (o1.. outers, h1.. holes)."""
    outers = sorted(
        [l for l in loops if l["depth"] % 2 == 0],
        key=lambda l: (-l["area"], l["bbox"][0], l["bbox"][1]),
    )
    holes = sorted(
        [l for l in loops if l["depth"] % 2 == 1],
        key=lambda l: (-l["bbox"][3], l["bbox"][0]),
    )
    table = []
    for i, l in enumerate(outers):
        table.append(_contour_row("o%d" % (i + 1), "outer", l))
    for i, l in enumerate(holes):
        table.append(_contour_row("h%d" % (i + 1), "hole", l))
    return table


def _contour_row(cid, kind, l):
    b = l["bbox"]
    c = l.get("circle")
    return {
        "id": cid,
        "kind": kind,
        "loop": l,
        "w": round(b[2] - b[0], 2),
        "h": round(b[3] - b[1], 2),
        "d": round(c["d"], 2) if c else None,
        "cx": round((b[0] + b[2]) / 2, 2),
        "cy": round((b[1] + b[3]) / 2, 2),
        "area": round(l["area"], 2),
        "length": round(_perimeter(l["pts"]), 2),
        "n": len(l["pts"]),
    }


def filter_pieces(loops, drop):
    """Remove the listed piece ids and everything nested inside them."""
    if not drop:
        return loops
    drop = {str(d) for d in drop}
    table = piece_table(loops)
    gone = [t["loop"] for t in table if t["id"] in drop]
    if not gone:
        return loops
    keep = []
    for l in loops:
        if any(l is g for g in gone):
            continue
        if any(_point_in_poly(l["pts"][0], g["pts"]) for g in gone if l["area"] < g["area"]):
            continue
        keep.append(l)
    return keep


def plan_cuts(loops, direction="ccw", drop=None, spec=None):
    """Order contours: inner (holes) first, then outers; nearest-neighbour.

    `spec` (optional) maps contour id -> {include, order, start_index, dir}.
    With a complete set of explicit orders the given sequence is used as-is;
    otherwise the automatic sequence is kept and only start/direction apply.

    Each contour is returned as {pts, pts0, kind, cid, order, length,
    start_index, dir}.
    """
    spec_in = spec
    loops = filter_pieces(loops, drop)
    table = contour_table(loops)
    spec = {str(k): v for k, v in (spec_in or {}).items()}
    chosen = [t for t in table if spec.get(t["id"], {}).get("include", True) is not False]
    if not chosen:
        return []

    automatic = _auto_sequence(chosen)
    if all(spec.get(t["id"], {}).get("order") is not None for t in chosen):
        seq = sorted(chosen, key=lambda t: int(spec[t["id"]]["order"]))
    else:
        seq = automatic

    ordered = []
    for t in seq:
        s = spec.get(t["id"], {})
        pts0 = list(t["loop"]["pts"])
        # requested turn direction is in the *displayed* frame:
        # screen clockwise == counter-clockwise in model coordinates.
        d = s.get("dir")
        if d in ("cw", "ccw"):
            want_model_ccw = d == "cw"
        else:
            want_model_ccw = direction == "ccw"
            if t["kind"] == "hole":
                want_model_ccw = not want_model_ccw
        is_ccw = _signed_area(pts0) > 0
        pts = _reverse(pts0) if is_ccw != want_model_ccw else pts0
        k = s.get("start_index")
        if isinstance(k, int) and 0 <= k < len(pts0):
            anchor = pts0[k]
        else:
            anchor = pts0[best_start_index(pts0)]
        pts = rotate_to_nearest(pts, anchor)
        start_index = _nearest_index(pts0, pts[0])
        ordered.append(
            {
                "pts": pts,
                "pts0": pts0,
                "kind": t["kind"],
                "cid": t["id"],
                "circle": t["loop"].get("circle") if t["kind"] == "hole" else None,
                "length": _perimeter(pts),
                "area": t["loop"]["area"],
                "start_index": start_index,
                "dir": "cw" if want_model_ccw else "ccw",
            }
        )
    for i, c in enumerate(ordered):
        c["order"] = i + 1
    return ordered


def _auto_sequence(table):
    """Holes first, then outlines; nearest neighbour inside each group."""
    holes = [t for t in table if t["kind"] == "hole"]
    outers = [t for t in table if t["kind"] == "outer"]

    def centre(t):
        pts = t["loop"]["pts"]
        return (
            sum(p[0] for p in pts) / len(pts),
            sum(p[1] for p in pts) / len(pts),
        )

    def chain(group, start):
        rest = list(group)
        cur = start
        out = []
        while rest:
            best, bi = 1e30, 0
            for i, t in enumerate(rest):
                c = centre(t)
                d = (c[0] - cur[0]) ** 2 + (c[1] - cur[1]) ** 2
                if d < best:
                    best, bi = d, i
            t = rest.pop(bi)
            out.append(t)
            cur = centre(t)
        return out

    if table:
        b0 = table[0]["loop"]["bbox"]
    else:
        b0 = [0, 0, 0, 0]
    cur = (b0[0], b0[1])
    seq = chain(holes, cur)
    seq += chain(outers, cur)
    return seq


def _nearest_index(pts, p):
    best, bi = 1e30, 0
    for i, q in enumerate(pts):
        d = (q[0] - p[0]) ** 2 + (q[1] - p[1]) ** 2
        if d < best:
            best, bi = d, i
    return bi


def _perimeter(xy):
    s = 0.0
    n = len(xy)
    for i in range(n):
        x1, y1 = xy[i]
        x2, y2 = xy[(i + 1) % n]
        s += math.hypot(x2 - x1, y2 - y1)
    return s


# ------------------------------------------------------------ arc primitives


def circle_through(p0, p1, p2):
    ax, ay = p0
    bx, by = p1
    cx, cy = p2
    d = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-12:
        return None
    ux = ((ax * ax + ay * ay) * (by - cy) + (bx * bx + by * by) * (cy - ay) + (cx * cx + cy * cy) * (ay - by)) / d
    uy = ((ax * ax + ay * ay) * (cx - bx) + (bx * bx + by * by) * (ax - cx) + (cx * cx + cy * cy) * (bx - ax)) / d
    r = math.hypot(ax - ux, ay - uy)
    return (ux, uy, r)


def path_segments(pts, arc_tol=0.01, allow_arcs=True):
    """Convert a point list into ('L',p) / ('A',r,large,sweep,p) steps.

    Arcs are only emitted where the source points really are a tessellation of
    a circle: residuals <= arc_tol and every chord short compared with r.
    """
    segs = []
    n = len(pts)
    if n < 3:
        for p in pts[1:]:
            segs.append(("L", p))
        return segs
    i = 0
    while i < n - 1:
        best = None
        if allow_arcs and i + 2 < n:
            c = circle_through(pts[i], pts[i + 1], pts[i + 2])
            if c and _run_ok(pts, i, i + 2, c, arc_tol):
                j = i + 2
                while j + 1 < n:
                    q = pts[j + 1]
                    if math.hypot(q[0] - pts[i][0], q[1] - pts[i][1]) < 1e-9:
                        break  # back at the start: never a >360 deg arc
                    c2 = _lsq_circle(pts[i:j + 2])
                    if c2 and _run_ok(pts, i, j + 1, c2, arc_tol):
                        j += 1
                        c = c2
                    else:
                        break
                if j - i >= 3:
                    best = (j, c)
        if best:
            j, c = best
            # use the larger endpoint radius so the renderer keeps our centre
            r_out = max(math.hypot(pts[i][0] - c[0], pts[i][1] - c[1]),
                        math.hypot(pts[j][0] - c[0], pts[j][1] - c[1]))
            c = (c[0], c[1], r_out)
            sweep = _sweep_flag(pts[i], pts[j], c)
            large = _large_arc(pts[i], pts[j], c, sweep)
            segs.append(("A", c[2], large, sweep, pts[j]))
            i = j
        else:
            segs.append(("L", pts[i + 1]))
            i += 1
    return segs


def _run_ok(pts, i, j, c, tol):
    ux, uy, r = c
    if r <= 0 or r > 1e5:
        return False
    prev = None
    for k in range(i, j + 1):
        p = pts[k]
        if abs(math.hypot(p[0] - ux, p[1] - uy) - r) > tol:
            return False
        if k > i:
            ch = math.hypot(p[0] - pts[k - 1][0], p[1] - pts[k - 1][1])
            if ch > 0.45 * r:
                return False
            if ch > 0 and prev is not None and prev != _sweep_flag(pts[i], p, c):
                return False
            if ch > 0:
                prev = _sweep_flag(pts[i], p, c)
    return True


def _lsq_circle(pts):
    """Algebraic (Kasa) least-squares circle fit."""
    n = len(pts)
    if n < 3:
        return None
    sx = sum(p[0] for p in pts)
    sy = sum(p[1] for p in pts)
    sxx = sum(p[0] * p[0] for p in pts)
    syy = sum(p[1] * p[1] for p in pts)
    sxy = sum(p[0] * p[1] for p in pts)
    sxz = sum(p[0] * (p[0] * p[0] + p[1] * p[1]) for p in pts)
    syz = sum(p[1] * (p[0] * p[0] + p[1] * p[1]) for p in pts)
    sz = sum(p[0] * p[0] + p[1] * p[1] for p in pts)
    a = [[sxx, sxy, sx], [sxy, syy, sy], [sx, sy, float(n)]]
    b = [-sxz, -syz, -sz]
    sol = _solve3(a, b)
    if not sol:
        return None
    dx, ey, f = sol
    cx, cy = -dx / 2, -ey / 2
    v = cx * cx + cy * cy - f
    if v <= 0:
        return None
    return (cx, cy, math.sqrt(v))


def _solve3(a, b):
    import copy

    m = [row[:] + [b[i]] for i, row in enumerate(copy.deepcopy(a))]
    for col in range(3):
        piv = max(range(col, 3), key=lambda r: abs(m[r][col]))
        if abs(m[piv][col]) < 1e-12:
            return None
        m[col], m[piv] = m[piv], m[col]
        for r in range(3):
            if r != col:
                f = m[r][col] / m[col][col]
                for k in range(col, 4):
                    m[r][k] -= f * m[col][k]
    return [m[i][3] / m[i][i] for i in range(3)]


def _sweep_flag(p0, p1, c):
    """sweep=1 means increasing angle in the emitted (y-down) frame."""
    cross = (p0[0] - c[0]) * (p1[1] - c[1]) - (p0[1] - c[1]) * (p1[0] - c[0])
    return 1 if cross > 0 else 0


def _large_arc(p0, p1, c, sweep):
    a0 = math.atan2(p0[1] - c[1], p0[0] - c[0])
    a1 = math.atan2(p1[1] - c[1], p1[0] - c[0])
    if sweep:
        d = (a1 - a0) % (2 * math.pi)
    else:
        d = (a0 - a1) % (2 * math.pi)
    return 1 if d > math.pi else 0


def _sweep_ok(p0, p1, p2, c):
    """Keep the arc monotonic (no direction flip) and < 2*pi."""
    s1 = _sweep_flag(p0, p1, c)
    s2 = _sweep_flag(p1, p2, c)
    return s1 == s2


def circle_of(pts, rel_tol=0.01, abs_tol=0.01):
    """Return (cx, cy, r) if the closed loop is a circle within tolerance."""
    if len(pts) < 8:
        return None
    cx = sum(p[0] for p in pts) / len(pts)
    cy = sum(p[1] for p in pts) / len(pts)
    rs = [math.hypot(p[0] - cx, p[1] - cy) for p in pts]
    rmean = sum(rs) / len(rs)
    if rmean < 0.15:
        return None
    if max(abs(r - rmean) for r in rs) > max(abs_tol, rel_tol * rmean):
        return None
    # centre of the bounding box must coincide with the centroid of a circle
    b = _pts_bbox(pts)
    if abs((b[0] + b[2]) / 2 - cx) > max(2 * abs_tol, 0.02 * rmean):
        return None
    if abs((b[1] + b[3]) / 2 - cy) > max(2 * abs_tol, 0.02 * rmean):
        return None
    return (cx, cy, rmean)
