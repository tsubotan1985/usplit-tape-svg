"""CLI: inspect an STL and emit an SVG (no web server).

  python3 -m app.cli faces  model.stl
  python3 -m app.cli svg    model.stl --face <id|index=0> -o out.svg [--offset 0.05]
"""
from __future__ import annotations

import argparse
import json
import sys

from . import geom, svgout


def load(path, min_area=0.8):
    tris = geom.read_stl(path)
    return tris, geom.detect_faces(tris, min_area=min_area)


def face_summary(f):
    holes = [l for l in f["loops"] if l["depth"] % 2 == 1]
    return {
        "id": f["id"],
        "axis": f["axis"],
        "offset": round(f["offset"], 3),
        "area": round(f["area"], 2),
        "outer": f["n_outer"],
        "holes": f["n_holes"],
        "hole_d": [round(h["circle"]["d"], 2) if h["circle"] else None for h in holes],
        "bbox": f["bbox"],
    }


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["faces", "svg", "png"])
    ap.add_argument("model")
    ap.add_argument("--face", default="0")
    ap.add_argument("-o", "--out", default=None)
    ap.add_argument("--dpi", type=int, default=400)
    ap.add_argument("--offset", type=float, default=0.0)
    ap.add_argument("--margin", type=float, default=4.0)
    ap.add_argument("--copies", type=int, default=1)
    ap.add_argument("--tabs", type=int, default=0)
    ap.add_argument("--drop", default="", help="捨てるピースID（カンマ区切り。facesの一覧は svg の stats.pieces）")
    ap.add_argument("--tail", type=float, default=0.0, help="輪郭の下側を延長する長さ(mm)。折り返し用")
    ap.add_argument("--mirror-x", action="store_true")
    ap.add_argument("--rotate", type=int, default=0)
    ap.add_argument("--min-area", type=float, default=0.8)
    a = ap.parse_args(argv)

    tris, faces = load(a.model, a.min_area)
    if a.cmd == "faces":
        for i, f in enumerate(faces):
            print(i, json.dumps(face_summary(f), ensure_ascii=False))
        return 0

    sel = None
    for i, f in enumerate(faces):
        if f["id"] == a.face or str(i) == a.face:
            sel = f
            break
    if not sel:
        print("face not found; run `faces` first", file=sys.stderr)
        return 2
    cuts = geom.plan_cuts(sel["loops"], direction="ccw", drop=[s for s in a.drop.split(",") if s])
    opts = {
        "offset_mm": a.offset,
        "margin_mm": a.margin,
        "copies": a.copies,
        "tabs": a.tabs,
        "mirror_x": a.mirror_x,
        "rotate": a.rotate,
        "tail_mm": a.tail,
        "arc_fit": True,
    }
    res = svgout.build(sel, cuts, opts, name=a.model.rsplit("/", 1)[-1])
    if a.cmd == "png":
        from . import pngout

        out = a.out or (a.model.rsplit(".", 1)[0] + ".png")
        info = pngout.svg_to_png(res["svg"], out, dpi=a.dpi)
        print(json.dumps({"stats": res["stats"], "png": info}, ensure_ascii=False, indent=1))
        return 0
    out = a.out or (a.model.rsplit(".", 1)[0] + ".svg")
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(res["svg"])
    print(json.dumps(res["stats"], ensure_ascii=False, indent=1))
    print("->", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
