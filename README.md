# uSPLIT Tape SVG

STLの平面を選び、カッティングマシン用のカット線SVG（実寸1:1・mm）を書き出すWebアプリ。
**どの線を・どこから・どの順で切るか**をGUIで指定できる。サーバ側はPython標準ライブラリだけで動く。

薄い板の平面に貼るガスケットや両面テープを、その面の外形＋穴の形に切り抜くために作った。

- 入力: バイナリ／ASCII STL（STEPはCADからSTLに書き出してから）
- 出力: SVG（`width="…mm"`、`viewBox` も mm）／PNG（300・600・1200 dpi、ブラウザ側で生成）
- 依存: Python 3.12 標準ライブラリのみ。numpy も trimesh も使わない

## 画面

1. **モデル** — STLを落とす
2. **平面を選ぶ** — 検出した平面をサムネイル・穴数・径・寸法つきで一覧。選ぶと即プレビュー
3. **カット順プレビュー** — カット順の番号、開始点（●）、シート枠。`▶ 順に再生` で刃の通り道を再生
4. **カット順と開始位置** — 線ごとに `上 / 下 / 左 / 右` で開始位置、`↻ ↺` で回転方向、
   `↑ ↓` で順番、`×` でその線を切らない。行を選んで**プレビューをクリック**すると任意の頂点が開始点になる
5. **出力設定** — ピース選択（同じ平面の独立領域を外す）、カット線補正、余白、個数、保持タブ、回転、反転
6. **結果** — シート寸法・ピース寸法・カット数・総カット長、SVG／PNGの保存

## 何をしているか

1. STLを読み、同一平面上の三角形をまとめて「面」にする（法線＋平面オフセットでグループ化）
2. 面ごとに境界エッジから輪郭ループを復元し、入れ子の深さで「外形」と「穴」に分ける
3. カット順を決める。既定は **穴（内側）が先、輪郭（外側）が後**、同種の中は直前の終点から近い順。
   各輪郭の入口は角（曲がりが大きい頂点）に寄せる。GUIで順番・開始位置・回転方向を上書きできる
4. テッセレーション由来の点列を円弧に置き換える（最小二乗で円を復元し、残差10µm・弦長が半径の45%以下のときだけ `A` コマンドにする）
5. mm実寸のSVGを書き出す。輪郭は `cut01, cut02, …` の順に並ぶので、グループ順（z順）を尊重する
   ソフトではその順に切られる

## 使い方

```bash
PORT=8000 DATA_DIR=./data python3 -m app.server     # http://127.0.0.1:8000
```

Docker:

```bash
docker build -t tape-svg:1.1.0 .
docker run -d --name tape-svg -p 8098:8000 -v "$PWD/data:/app/data" tape-svg:1.1.0
# もしくは docker compose up -d
```

- アップロードは `./data/jobs/<job>/<model>.stl`、生成物は `./data/out/*.svg` に残る
- ヘルスチェック: `GET /api/health`

## API

```
POST /api/analyze   multipart/form-data: file=<STL>
  -> { job, model, tris, bbox, n_faces, faces:[{id, axis, offset, area, n_holes, hole_d[], w, h, thumb}] }

POST /api/svg       { job, face, opts }
  -> { svg, stats }
```

`opts` の主なもの:

| key | 意味 |
|---|---|
| `offset_mm` | カット線補正。+で部品が大きくなる（外形は外へ、穴は内へ） |
| `margin_mm` | シート外周の余白 |
| `copies` / `copy_gap_mm` | 同じピースを並べる個数と間隔 |
| `tabs` / `tab_len_mm` | 輪郭に残す保持タブの数と幅 |
| `rotate` / `mirror_x` / `mirror_y` | 回転・反転 |
| `arc_fit` | 円弧化の有無 |
| `guides` | 10mm基準線を隠しグループで入れる |
| `drop_pieces` | 外すピースID（`stats.pieces` の id） |
| `contours` | `[{id, include, order, start_index, dir}]`。カット順・開始頂点・回転方向の上書き |

`stats` には `contours`（面の全輪郭の `id`・寸法・`pts`）と `order`（実際のカット順と開始点）が入る。
`start_index` は `contours[].pts` の頂点番号で、プレビューのクリック指定はこの番号を送っている。

## CLI（サーバを立てずに1枚だけ作る）

```bash
python3 -m app.cli faces model.stl                      # 面の一覧
python3 -m app.cli svg   model.stl --face 0 --drop 2 -o tape.svg
python3 -m app.cli png   model.stl --face 0 --drop 2 -o tape.png --dpi 600
```

## 自己検証

生成したSVGを読み直して、元の輪郭との差（最大偏差・面積誤差）とカット順を検算する。

```bash
python3 -m tests.verify model.stl 0
python3 -m tests.verify model.stl 0 --offset 0.05    # 補正でシートが+0.1mm、穴が-0.1mmになることも確認
```

## 注意

- カット順はSVGの**グループ順**で表現している。Cricut Design Space のように内部で切断順を
  最適化するソフトでは、その順にならないことがある。Silhouette Studio・LightBurn・
  Graphtec/Flexi などはオブジェクト順を尊重する。
- `10mm基準線` は `<g id="GUIDES" style="display:none">` として入る。実寸確認に使い、カット前に消す。
- STLは単位を持たない。外形寸法が想定と桁違いなら、CAD側の書き出し単位を疑う。
- PNGはブラウザの canvas で作る（サーバに画像ライブラリを持たせない）。

## 開発メモ

- `app/geom.py` 三角形のグループ化・ループ復元・入れ子判定・オフセット・円弧復元・カット順
- `app/svgout.py` SVG生成（mm実寸、y反転、タブ分割、複数個）
- `app/pngout.py` サーバ側のPNG書き出し（CLI用。Pillowが必要）
- `app/server.py` 標準ライブラリのHTTPサーバ（multipartの自前パース込み）

## License

MIT
