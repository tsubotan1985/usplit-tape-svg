"""uSPLIT Tape SVG - tiny stdlib web server (no third-party deps)."""
from __future__ import annotations

import json
import mimetypes
import os
import re
import secrets
import shutil
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import geom, svgout

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("DATA_DIR", os.path.join(os.path.dirname(ROOT), "data"))
STATIC = os.path.join(ROOT, "static")
VERSION = "1.3.0"
MAX_UPLOAD = 60 * 1024 * 1024
MAX_FACES = 20


def log(*a):
    print("[%s]" % time.strftime("%H:%M:%S"), *a, flush=True)


def _job_dir(job):
    if not re.fullmatch(r"[0-9a-f]{12}", job or ""):
        raise ValueError("bad job id")
    d = os.path.join(DATA, "jobs", job)
    if not os.path.isdir(d):
        raise ValueError("job not found")
    return d


def _model_path(job):
    d = _job_dir(job)
    for f in sorted(os.listdir(d)):
        if f.lower().endswith((".stl", ".stla", ".step", ".stp")):
            return os.path.join(d, f)
    raise ValueError("model file missing")


def analyze(job):
    path = _model_path(job)
    if path.lower().endswith((".step", ".stp")):
        raise ValueError(
            "STEPには未対応です。CADからSTL（バイナリ推奨）で書き出してから読み込んでください。"
        )
    t0 = time.time()
    tris = geom.read_stl(path)
    faces = geom.detect_faces(tris)
    bbox = _mesh_bbox(tris)
    out = []
    for i, f in enumerate(faces[:MAX_FACES]):
        holes = [l for l in f["loops"] if l["depth"] % 2 == 1]
        out.append(
            {
                "id": f["id"],
                "index": i,
                "axis": f["axis"],
                "offset": round(f["offset"], 2),
                "area": round(f["area"], 2),
                "n_outer": f["n_outer"],
                "n_holes": f["n_holes"],
                "hole_d": [round(h["circle"]["d"], 2) if h.get("circle") else 0 for h in holes],
                "w": round(f["bbox"][2] - f["bbox"][0], 2),
                "h": round(f["bbox"][3] - f["bbox"][1], 2),
                "thumb": svgout.thumbnail(f),
            }
        )
    return {
        "model": os.path.basename(path),
        "size_mb": round(os.path.getsize(path) / 1e6, 2),
        "tris": len(tris),
        "bbox": bbox,
        "n_faces": len(faces),
        "faces": out,
        "sec": round(time.time() - t0, 2),
    }


def _mesh_bbox(tris):
    xs = [p[0] for t in tris for p in t]
    ys = [p[1] for t in tris for p in t]
    zs = [p[2] for t in tris for p in t]
    return [round(min(xs), 2), round(min(ys), 2), round(min(zs), 2),
            round(max(xs), 2), round(max(ys), 2), round(max(zs), 2)]


def make_svg(job, face_id, opts):
    path = _model_path(job)
    tris = geom.read_stl(path)
    faces = geom.detect_faces(tris)
    face = next((f for f in faces if f["id"] == face_id), None)
    if not face:
        raise ValueError("指定の面が見つかりません（モデルを読み直してください）")
    opts = dict(opts or {})
    # 既定では「いちばん大きいピース」だけを残す（同じ平面の付随的な帯を切らない）
    if opts.get("drop_pieces") is None:
        pieces = geom.piece_table(face["loops"])
        drop = [p["id"] for p in pieces[1:]]
    else:
        drop = [str(d) for d in (opts.get("drop_pieces") or [])]
    spec = {}
    for c in opts.get("contours") or []:
        if isinstance(c, dict) and c.get("id"):
            spec[str(c["id"])] = {k: c[k] for k in ("include", "order", "start_index", "dir") if k in c}
    cuts = geom.plan_cuts(face["loops"], direction=opts.get("direction", "ccw"), drop=drop, spec=spec)
    if not cuts:
        raise ValueError("この設定ではカット線が残りません（カットの選択を見直してください）")
    res = svgout.build(face, cuts, opts, name=os.path.basename(path))
    stats = res["stats"]
    stats["face"] = {"axis": face["axis"], "offset": round(face["offset"], 2),
                     "n_holes": face["n_holes"], "n_outer": face["n_outer"]}
    stats["pieces"] = [
        {"id": t["id"], "w": t["w"], "h": t["h"], "area": t["area"], "holes": t["holes"]}
        for t in geom.piece_table(face["loops"])
    ]
    applied = {c["cid"]: c for c in cuts}
    pts_by_id = {r["id"]: r.get("pts") for r in stats["order"] if r.get("id") and r.get("pts")}
    tail = float(stats.get("tail_mm") or 0.0)
    contours = []
    for t in geom.contour_table(face["loops"]):
        a = applied.get(t["id"])
        # 折り返しを伸ばした外形は、その分だけ縦に長くなる
        ch = round(t["h"] + (tail if (tail > 0 and t["kind"] == "outer") else 0.0), 2)
        contours.append(
            {
                "id": t["id"],
                "kind": t["kind"],
                "d": t["d"],
                "w": t["w"],
                "h": ch,
                "length": t["length"],
                "area": t["area"],
                "include": a is not None,
                "order": a["order"] if a else None,
                "start_index": a["start_index"] if a else None,
                "dir": a["dir"] if a else None,
                "pts": pts_by_id.get(t["id"]),
            }
        )
    stats["contours"] = contours
    stats["dropped"] = drop
    stats["opts"] = opts
    # keep the artefact
    try:
        d = os.path.join(DATA, "out")
        os.makedirs(d, exist_ok=True)
        name = "%s_%s_%s.svg" % (job, face_id, time.strftime("%Y%m%d-%H%M%S"))
        with open(os.path.join(d, name), "w", encoding="utf-8") as fh:
            fh.write(res["svg"])
        stats["saved"] = name
    except OSError:
        pass
    return {"svg": res["svg"], "stats": stats}


# ------------------------------------------------------------------ multipart


def parse_multipart(body, ctype):
    m = re.search(r'boundary="?([^";]+)"?', ctype or "")
    if not m:
        raise ValueError("multipart boundary がありません")
    boundary = b"--" + m.group(1).encode()
    parts = body.split(boundary)
    for p in parts:
        if b"\r\n\r\n" not in p:
            continue
        head, data = p.split(b"\r\n\r\n", 1)
        head_s = head.decode("utf-8", "replace")
        fm = re.search(r'filename="([^"]*)"', head_s)
        if not fm:
            continue
        data = data.rstrip(b"\r\n")
        return fm.group(1), data
    raise ValueError("ファイルが含まれていません")


# ------------------------------------------------------------------- handler


class Handler(BaseHTTPRequestHandler):
    server_version = "uSPLITTapeSVG/" + VERSION
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        log("%s %s" % (self.address_string(), fmt % args))

    # ---- helpers
    def _send(self, code, body, ctype="application/json; charset=utf-8", extra=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False))

    def _err(self, code, msg):
        self._json({"error": msg}, code)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_UPLOAD:
            raise ValueError("ファイルが大きすぎます（上限 %d MB）" % (MAX_UPLOAD // 1024 // 1024))
        return self.rfile.read(n) if n else b""

    # ---- routes
    def do_GET(self):
        try:
            p = self.path.split("?")[0]
            if p in ("/", "/index.html"):
                return self._static("index.html")
            if p == "/api/health":
                return self._json({"ok": True, "version": VERSION,
                                   "data": DATA, "jobs": _count(os.path.join(DATA, "jobs"))})
            if p.startswith("/static/"):
                return self._static(p[len("/static/"):])
            if p == "/favicon.ico":
                return self._send(204, b"", "image/x-icon")
            return self._err(404, "not found")
        except Exception as e:  # pragma: no cover
            traceback.print_exc()
            return self._err(500, str(e))

    def do_POST(self):
        try:
            p = self.path.split("?")[0]
            if p == "/api/analyze":
                return self._analyze()
            if p == "/api/svg":
                req = json.loads(self._body() or b"{}")
                job = req.get("job", "")
                if not req.get("face"):
                    return self._err(400, "面を選んでください")
                return self._json(make_svg(job, req["face"], req.get("opts") or {}))
            return self._err(404, "not found")
        except ValueError as e:
            return self._err(400, str(e))
        except Exception as e:  # pragma: no cover
            traceback.print_exc()
            return self._err(500, str(e))

    # ---- impl
    def _analyze(self):
        name, data = parse_multipart(self._body(), self.headers.get("Content-Type", ""))
        ext = os.path.splitext(name)[1].lower()
        if ext not in (".stl", ".step", ".stp"):
            return self._err(400, "STL（またはSTEP）を選んでください: " + (name or "?"))
        job = secrets.token_hex(6)
        d = os.path.join(DATA, "jobs", job)
        os.makedirs(d, exist_ok=True)
        safe = re.sub(r"[^\w.\-]", "_", os.path.basename(name))[:80]
        with open(os.path.join(d, safe), "wb") as fh:
            fh.write(data)
        try:
            info = analyze(job)
        except ValueError as e:
            shutil.rmtree(d, ignore_errors=True)
            return self._err(400, str(e))
        info["job"] = job
        log("analyze", job, name, len(data) // 1024, "kB")
        return self._json(info)

    def _static(self, rel):
        rel = rel.split("?")[0]
        path = os.path.normpath(os.path.join(STATIC, rel))
        if not path.startswith(STATIC) or not os.path.isfile(path):
            return self._err(404, "not found")
        ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript",):
            ctype += "; charset=utf-8"
        with open(path, "rb") as fh:
            body = fh.read()
        return self._send(200, body, ctype)


def _count(p):
    try:
        return len(os.listdir(p))
    except OSError:
        return 0


def main():
    os.makedirs(os.path.join(DATA, "jobs"), exist_ok=True)
    os.makedirs(os.path.join(DATA, "out"), exist_ok=True)
    port = int(os.environ.get("PORT", 8000))
    srv = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    log("uSPLIT Tape SVG %s listening on :%d  data=%s" % (VERSION, port, DATA))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
