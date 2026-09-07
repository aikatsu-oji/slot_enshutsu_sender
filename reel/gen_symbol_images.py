# 画像生成 AI で図柄を1枚ずつ作り、reel/img/<id>.png に置いて差し替えるまでを自動化する
#
#   python reel/gen_symbol_images.py                       # 全図柄を生成 → 背景除去 → symbol_images.js 再生成
#   python reel/gen_symbol_images.py --only bell,seven     # 一部だけ
#   python reel/gen_symbol_images.py --ref reel/img/bell.png   # 既存画像を参照させて絵柄を揃える
#   python reel/gen_symbol_images.py --design C:/slot_design   # 仕上げに node gen.mjs も実行 (デザインキャンバス更新)
#   python reel/gen_symbol_images.py --dry-run             # プロンプトだけ表示
#
# プロンプトは reel/symbol_prompts.json。生成元の画像は reel/img/raw/<id>_<日時>.png に残す。
#
# プロバイダ (環境変数のキーで自動判別、--provider で明示):
#   openai : OPENAI_API_KEY   gpt-image-1  (透過背景で直接出力)
#   gemini : GEMINI_API_KEY / GOOGLE_API_KEY   gemini-2.5-flash-image  (白背景で生成 → 外周をフラッドフィルで透過)
#   pip install requests pillow google-genai
import argparse
import base64
import io
import json
import os
import subprocess
import sys
import time
from collections import deque

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
IMG_DIR = os.path.join(HERE, "img")
RAW_DIR = os.path.join(IMG_DIR, "raw")
PROMPTS = os.path.join(HERE, "symbol_prompts.json")


# ---------------------------------------------------------------- プロバイダ
def gen_openai(prompt, size, ref_paths, transparent):
    import requests

    key = os.environ["OPENAI_API_KEY"]
    headers = {"Authorization": f"Bearer {key}"}
    common = {"model": "gpt-image-1", "size": size, "n": 1, "output_format": "png", "quality": "high"}
    if transparent:
        common["background"] = "transparent"
    if ref_paths:
        files = [("image[]", (os.path.basename(p), open(p, "rb"), "image/png")) for p in ref_paths]
        r = requests.post("https://api.openai.com/v1/images/edits", headers=headers,
                          data={**common, "prompt": prompt}, files=files, timeout=300)
    else:
        r = requests.post("https://api.openai.com/v1/images/generations", headers=headers,
                          json={**common, "prompt": prompt}, timeout=300)
    if r.status_code != 200:
        raise RuntimeError(f"openai {r.status_code}: {r.text[:400]}")
    return base64.b64decode(r.json()["data"][0]["b64_json"])


def gen_gemini(prompt, size, ref_paths, transparent):
    from google import genai
    from google.genai import types

    key = os.environ.get("GEMINI_API_KEY") or os.environ["GOOGLE_API_KEY"]
    client = genai.Client(api_key=key)
    contents = [prompt]
    for p in ref_paths:
        contents.append(Image.open(p))
    res = client.models.generate_content(
        model=os.environ.get("GEMINI_IMAGE_MODEL", "gemini-2.5-flash-image"),
        contents=contents,
        config=types.GenerateContentConfig(response_modalities=["IMAGE"]),
    )
    for part in res.candidates[0].content.parts:
        if getattr(part, "inline_data", None) and part.inline_data.data:
            return part.inline_data.data
    raise RuntimeError("gemini: 画像が返りませんでした: " + str(res)[:400])


PROVIDERS = {
    "openai": {"fn": gen_openai, "env": ["OPENAI_API_KEY"], "transparent": True},
    "gemini": {"fn": gen_gemini, "env": ["GEMINI_API_KEY", "GOOGLE_API_KEY"], "transparent": False},
}


def detect_provider():
    for name, p in PROVIDERS.items():
        if any(os.environ.get(e) for e in p["env"]):
            return name
    return None


# ---------------------------------------------------------------- 背景除去
def remove_background(im, tol=40):
    """外周から辿れる、四隅の色に近い画素だけを透過にする (図柄内部の白は残る)"""
    im = im.convert("RGBA")
    w, h = im.size
    px = im.load()
    corners = [px[0, 0], px[w - 1, 0], px[0, h - 1], px[w - 1, h - 1]]
    bg = tuple(sum(c[i] for c in corners) // 4 for i in range(3))

    def is_bg(p):
        return abs(p[0] - bg[0]) + abs(p[1] - bg[1]) + abs(p[2] - bg[2]) <= tol * 3

    seen = bytearray(w * h)
    q = deque()
    for x in range(w):
        q.append((x, 0)); q.append((x, h - 1))
    for y in range(h):
        q.append((0, y)); q.append((w - 1, y))
    while q:
        x, y = q.popleft()
        i = y * w + x
        if seen[i]:
            continue
        seen[i] = 1
        if not is_bg(px[x, y]):
            continue
        px[x, y] = (0, 0, 0, 0)
        if x > 0: q.append((x - 1, y))
        if x < w - 1: q.append((x + 1, y))
        if y > 0: q.append((x, y - 1))
        if y < h - 1: q.append((x, y + 1))
    return im


def has_transparency(im):
    return im.mode == "RGBA" and im.split()[3].getextrema()[0] < 255


# ---------------------------------------------------------------- 本体
def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")   # cp932 コンソールでも日本語を出す
    ap = argparse.ArgumentParser(description="画像生成 AI で図柄を作って差し替える")
    ap.add_argument("--provider", choices=list(PROVIDERS), default=None)
    ap.add_argument("--only", default="", help="生成する図柄 id をカンマ区切りで (例: bell,seven)")
    ap.add_argument("--ref", action="append", default=[], help="参照画像 (複数可)。絵柄を揃えたいときに既存の図柄を渡す")
    ap.add_argument("--design", default="", help="slot_design フォルダ。指定すると最後に node gen.mjs を実行")
    ap.add_argument("--no-build", action="store_true", help="symbol_images.js の再生成をしない")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--retries", type=int, default=2)
    args = ap.parse_args()

    spec = json.load(open(PROMPTS, encoding="utf-8"))
    only = {s for s in args.only.split(",") if s}
    targets = [s for s in spec["symbols"] if not only or s["id"] in only]
    if only and len(targets) != len(only):
        sys.exit("不明な id: " + ",".join(only - {s["id"] for s in targets}))

    provider = args.provider or detect_provider()
    if not provider and not args.dry_run:
        sys.exit("API キーがありません。OPENAI_API_KEY か GEMINI_API_KEY を設定してください")
    transparent = PROVIDERS[provider]["transparent"] if provider else True

    os.makedirs(RAW_DIR, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    for s in targets:
        prompt = f"{spec['style']}. {s['subject']}. "
        prompt += "transparent background" if transparent else spec["background"]
        if args.ref:
            prompt += ". Match the rendering style, lighting and proportions of the reference image exactly"
        print(f"[{s['id']}] {s['name']}: {prompt}")
        if args.dry_run:
            continue
        data = None
        for attempt in range(args.retries + 1):
            try:
                data = PROVIDERS[provider]["fn"](prompt, spec.get("size", "1024x1024"), args.ref, transparent)
                break
            except Exception as e:  # noqa: BLE001
                print(f"  失敗 ({attempt + 1}/{args.retries + 1}): {e}")
                time.sleep(3)
        if data is None:
            sys.exit(f"[{s['id']}] 生成できませんでした")
        raw_path = os.path.join(RAW_DIR, f"{s['id']}_{stamp}.png")
        open(raw_path, "wb").write(data)
        im = Image.open(io.BytesIO(data))
        if not has_transparency(im):
            im = remove_background(im)
        out = os.path.join(IMG_DIR, s["id"] + ".png")
        im.save(out)
        print(f"  -> {os.path.relpath(out, HERE)}  (元: {os.path.relpath(raw_path, HERE)})")

    if args.dry_run or args.no_build:
        return
    subprocess.run([sys.executable, os.path.join(HERE, "build_symbol_images.py")], check=True)
    if args.design:
        subprocess.run(["node", "gen.mjs"], cwd=args.design, check=True, shell=(os.name == "nt"))
    print("完了。筐体ビュー / 図柄カタログを再読込すると新しい図柄になります")


if __name__ == "__main__":
    main()
