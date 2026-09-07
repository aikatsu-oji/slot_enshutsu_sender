// 図柄定義 (筐体ビュー / 図柄カタログ / デザイン書き出しで共有)
//
//   - 全図柄 viewBox 0 0 240 80。コマ (横292×縦107 @ユニット幅900px) の 82%×74% に収まる比率
//   - 色は reel.html の CSS 変数と同じ値をリテラルで持つ (単体 SVG として書き出せるようにするため)
//   - ページには sprite() を1回だけ挿入し、各コマは use() で <use href="#sym-xxx"> 参照する
//     (1リール21コマ + 継ぎ目複製で 26要素 × 3リール = 78個。丸ごと埋め込むと gradient の id が衝突する)
//   - 通常スクリプトとして読み込む。globalThis.SlotSymbols に公開 (node からも import './symbols.js' で使える)
//   - symbol_images.js (data URI) を先に読み込むと、画像がある図柄は <image> で描く。無い図柄は従来のベクター描画
//     画像は reel/img/<id>.png → build_symbol_images.py。画像生成 AI で作るなら gen_symbol_images.py
(function (root) {
  "use strict";

  const IMG = root.SlotSymbolImages || {};
  /** 画像を viewBox いっぱいに収める <image> (縦横比維持・中央寄せ) */
  function image(key) {
    const m = IMG[key];
    if (!m) return null;
    return `<image href="${m.src}" x="0" y="0" width="240" height="80" preserveAspectRatio="xMidYMid meet"></image>`;
  }

  const C = {
    frame: "#c9a24a", frameBright: "#f1d27a", frameDim: "#7d6328", frameShadow: "#0b1226",
    red: "#d43a2f", redHi: "#ff7f74", redDk: "#7a1811",
    green: "#3fa35b", greenHi: "#6cc47f", greenDk: "#1f6b35",
    yellow: "#f2c23b", yellowHi: "#ffe27a", yellowDk: "#c8931a", yellowInk: "#8a5f00",
    blue: "#4d8fd6", blueHi: "#7fb0ea", blueInk: "#e6f0ff",
    white: "#ffffff", blankA: "#dcd3bd", blankB: "#efe6d2",
  };
  const FONT_SYM = "'Yu Mincho', 'Hiragino Mincho ProN', serif";
  const FONT_BOLD = "Impact, 'Arial Black', sans-serif";
  const VIEWBOX = "0 0 240 80";

  // 図柄名 (主制御と共通) → id / 表示情報
  const INFO = {
    "神":       { id: "god",    en: "GOD",    role: "神揃い → GG突入・ストック+5", pay: "払出 0枚" },
    "赤7":      { id: "seven",  en: "SEVEN",  role: "赤7揃い → ストック+1 (GG中)", pay: "払出 0枚" },
    "ベル":     { id: "bell",   en: "BELL",   role: "共通ベル / 押順ベル(6択)",   pay: "払出 8枚" },
    "リプ":     { id: "rep",    en: "REPLAY", role: "リプレイ (再遊技)",          pay: "払出 3枚" },
    "スイカ":   { id: "melon",  en: "MELON",  role: "スイカ (レア役)",            pay: "払出 3枚" },
    "ブランク": { id: "blank",  en: "BLANK",  role: "ブランク図柄 (入賞役なし)",  pay: "—" },
  };
  const ORDER = Object.keys(INFO);

  // 図柄の中身 (viewBox 240×80 座標系)。gradient の id は図柄ごとに一意
  const BODY = {
    "神": image("god") || `
<defs><linearGradient id="g-god" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#fff3c8"></stop><stop offset="0.45" stop-color="${C.frameBright}"></stop><stop offset="1" stop-color="${C.frame}"></stop></linearGradient></defs>
<rect x="14" y="4" width="212" height="70" rx="8" fill="${C.frameDim}"></rect>
<rect x="14" y="2" width="212" height="68" rx="8" fill="url(#g-god)" stroke="${C.frameDim}" stroke-width="2"></rect>
<rect x="24" y="11" width="192" height="50" rx="4" fill="${C.red}" stroke="${C.redDk}" stroke-width="2"></rect>
<rect x="28" y="15" width="184" height="42" rx="3" fill="none" stroke="${C.frameBright}" stroke-width="1.5" opacity="0.7"></rect>
<text x="120" y="54" text-anchor="middle" font-family="${FONT_SYM}" font-weight="700" font-size="40" fill="${C.redDk}">神</text>
<text x="120" y="52" text-anchor="middle" font-family="${FONT_SYM}" font-weight="700" font-size="40" fill="${C.frameBright}">神</text>`,
    "赤7": image("seven") || `
<defs><linearGradient id="g-seven" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="${C.redHi}"></stop><stop offset="0.5" stop-color="${C.red}"></stop><stop offset="1" stop-color="${C.redDk}"></stop></linearGradient></defs>
<path d="M66,13 H190 L194,27 L130,79 H94 L152,29 H70 Z" fill="${C.redDk}"></path>
<path d="M66,10 H190 L194,24 L130,76 H94 L152,26 H70 Z" fill="url(#g-seven)" stroke="${C.white}" stroke-width="2.5" stroke-linejoin="round"></path>
<path d="M76,15 H182 L179,20 H80 Z" fill="${C.white}" opacity="0.55"></path>`,
    "ベル": image("bell") || `
<defs><radialGradient id="g-bell" cx="0.5" cy="0.35" r="0.65"><stop offset="0" stop-color="${C.yellowHi}"></stop><stop offset="0.6" stop-color="${C.yellow}"></stop><stop offset="1" stop-color="${C.yellowDk}"></stop></radialGradient></defs>
<circle cx="120" cy="11" r="6" fill="${C.yellow}" stroke="${C.yellowInk}" stroke-width="2"></circle>
<path d="M90,58 Q88,26 120,20 Q152,26 150,58 L162,68 H78 Z" fill="url(#g-bell)" stroke="${C.yellowInk}" stroke-width="2" stroke-linejoin="round"></path>
<circle cx="120" cy="72" r="6.5" fill="${C.yellowDk}" stroke="${C.yellowInk}" stroke-width="2"></circle>
<path d="M102,38 Q108,27 118,26" fill="none" stroke="${C.white}" stroke-width="4" stroke-linecap="round" opacity="0.7"></path>`,
    "リプ": image("rep") || `
<defs><linearGradient id="g-rep" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="${C.blueHi}"></stop><stop offset="1" stop-color="${C.blue}"></stop></linearGradient></defs>
<rect x="46" y="18" width="148" height="44" rx="22" fill="url(#g-rep)" stroke="${C.white}" stroke-width="2"></rect>
<path d="M66,46 A16,16 0 0 1 92,30" fill="none" stroke="${C.blueInk}" stroke-width="5" stroke-linecap="round"></path>
<path d="M96,34 A16,16 0 0 1 70,50" fill="none" stroke="${C.blueInk}" stroke-width="5" stroke-linecap="round"></path>
<path d="M92,22 L102,31 L88,36 Z" fill="${C.blueInk}"></path>
<path d="M70,58 L60,49 L74,44 Z" fill="${C.blueInk}"></path>
<text x="148" y="50" text-anchor="middle" font-family="${FONT_BOLD}" font-size="28" letter-spacing="3" fill="${C.blueInk}">RP</text>`,
    "スイカ": image("melon") || `
<path d="M58,70 A62,62 0 0 1 182,70 Z" fill="${C.greenDk}"></path>
<path d="M68,70 A52,52 0 0 1 172,70 Z" fill="${C.greenHi}"></path>
<path d="M76,70 A44,44 0 0 1 164,70 Z" fill="${C.red}"></path>
<ellipse cx="104" cy="52" rx="3.5" ry="6" fill="${C.frameShadow}" transform="rotate(-20 104 52)"></ellipse>
<ellipse cx="120" cy="42" rx="3.5" ry="6" fill="${C.frameShadow}"></ellipse>
<ellipse cx="136" cy="52" rx="3.5" ry="6" fill="${C.frameShadow}" transform="rotate(20 136 52)"></ellipse>
<ellipse cx="120" cy="60" rx="3.5" ry="6" fill="${C.frameShadow}"></ellipse>
<line x1="56" y1="70" x2="184" y2="70" stroke="${C.greenDk}" stroke-width="3" stroke-linecap="round"></line>`,
    "ブランク": image("blank") || `
<defs><pattern id="p-blank" width="8" height="8" patternUnits="userSpaceOnUse" patternTransform="rotate(135)"><rect width="4" height="8" fill="${C.blankA}"></rect><rect x="4" width="4" height="8" fill="${C.blankB}"></rect></pattern></defs>
<rect x="36" y="24" width="168" height="32" fill="url(#p-blank)" stroke="${C.blankA}" stroke-width="1"></rect>`,
  };

  const NS = 'xmlns="http://www.w3.org/2000/svg"';

  /** 単体の SVG 文字列 (書き出し・カタログ用) */
  function svg(name, w, h, attrs) {
    if (!(name in BODY)) throw new Error("unknown symbol: " + name);
    const size = w != null ? ` width="${w}" height="${h != null ? h : Math.round(w / 3)}"` : "";
    return `<svg viewBox="${VIEWBOX}"${size} ${NS}${attrs ? " " + attrs : ""}>${BODY[name]}</svg>`;
  }

  /** 全図柄を <symbol> にまとめたスプライト。ページに1回だけ入れる */
  function sprite() {
    const syms = ORDER.map((n) => `<symbol id="sym-${INFO[n].id}" viewBox="${VIEWBOX}">${BODY[n]}</symbol>`).join("");
    return `<svg id="slot-symbol-sprite" ${NS} style="position:absolute;width:0;height:0;overflow:hidden" aria-hidden="true">${syms}</svg>`;
  }

  /** スプライト参照用の <svg><use> 文字列。cls に reel.html の .sym などを渡す */
  function use(name, cls) {
    if (!(name in INFO)) throw new Error("unknown symbol: " + name);
    return `<svg class="${cls || "sym"}" viewBox="${VIEWBOX}" ${NS}><use href="#sym-${INFO[name].id}"></use></svg>`;
  }

  /** document にスプライトを挿入する (2回目以降は何もしない) */
  function install(doc) {
    const d = doc || (typeof document !== "undefined" ? document : null);
    if (!d || d.getElementById("slot-symbol-sprite")) return;
    const host = d.createElement("div");
    host.innerHTML = sprite();
    d.body.insertBefore(host.firstElementChild, d.body.firstChild);
  }

  root.SlotSymbols = { VIEWBOX, PALETTE: C, INFO, ORDER, body: (n) => BODY[n], svg, sprite, use, install };
})(typeof globalThis !== "undefined" ? globalThis : window);
