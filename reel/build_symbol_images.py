# 図柄画像 → reel/symbol_images.js (data URI) を生成する
#   python reel/build_symbol_images.py [元画像...]
#   元画像は透過 PNG。非透過部分で切り抜き、幅 WIDTH px に縮小して 256 色パレット PNG にする
import base64, os, sys
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
WIDTH = 600
PAD = 6
# 図柄 id → 元画像。reel/img/<id>.png があるものだけ (id は symbols.js の INFO と同じ: god/seven/bell/rep/melon/blank)
IDS = ("god", "seven", "bell", "rep", "melon", "blank")
SOURCES = {k: p for k in IDS if os.path.exists(p := os.path.join(HERE, "img", k + ".png"))}


def build(path):
    im = Image.open(path).convert("RGBA")
    b = im.split()[3].getbbox()
    if b:
        im = im.crop((max(0, b[0] - PAD), max(0, b[1] - PAD), min(im.width, b[2] + PAD), min(im.height, b[3] + PAD)))
    w = WIDTH
    h = round(im.height * w / im.width)
    im = im.resize((w, h), Image.LANCZOS).quantize(256, method=Image.Quantize.FASTOCTREE)
    out = os.path.join(HERE, "img", os.path.basename(path).replace(".png", ".min.png"))
    im.save(out, optimize=True)
    data = base64.b64encode(open(out, "rb").read()).decode()
    return w, h, data, os.path.getsize(out)


def main():
    entries = []
    for key, path in SOURCES.items():
        w, h, data, size = build(path)
        print(f"{key}: {w}x{h} {size} bytes")
        entries.append(f'    {key}: {{ w: {w}, h: {h}, src: "data:image/png;base64,{data}" }},')
    js = (
        "// 図柄画像 (data URI)。symbols.js より先に読み込む。build_symbol_images.py が生成 (手で編集しない)\n"
        "//   元画像: reel/img/<id>.png (透過 PNG)。再生成: python reel/build_symbol_images.py\n"
        "(function (root) {\n  root.SlotSymbolImages = {\n" + "\n".join(entries) + "\n  };\n"
        '})(typeof globalThis !== "undefined" ? globalThis : window);\n'
    )
    with open(os.path.join(HERE, "symbol_images.js"), "w", encoding="utf-8") as f:
        f.write(js)
    print("wrote reel/symbol_images.js", os.path.getsize(os.path.join(HERE, "symbol_images.js")))


if __name__ == "__main__":
    main()
