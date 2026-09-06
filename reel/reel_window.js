// リール窓の生成 (筐体ビュー reel.html と図柄カタログ symbols.html で共有)
//
//   - 配列は main_board/reels.json を fetch する (主制御と同じ唯一の定義)
//   - ReelView は DOM の構築 (build) と停止位置の描画 (render) だけを持つ。回転の物理・停止制御・
//     主制御連動は reel.html 側 (Reel extends ReelView) に残す
//   - コマの高さは CSS 変数 --koma、リール窓は3コマ分。番号が大きいコマほど上に来る
//   - 依存: symbols.js (SlotSymbols) を先に読み込むこと
(function (root) {
  "use strict";

  /** 配列を読み込む。{koma, reels:[[...],[...],[...]]} を返す */
  async function load(url) {
    const res = await fetch(url || "../main_board/reels.json", { cache: "no-store" });
    if (!res.ok) throw new Error(`reels.json の取得に失敗 (${res.status})`);
    const d = await res.json();
    if (!Array.isArray(d.reels) || d.reels.length !== 3) throw new Error("reels.json: reels は3本必要");
    d.reels.forEach((t, i) => {
      if (t.length !== d.koma) throw new Error(`reels.json: リール${i} のコマ数が ${d.koma} ではありません`);
      t.forEach((s) => { if (!root.SlotSymbols.INFO[s]) throw new Error(`reels.json: 未定義の図柄 "${s}"`); });
    });
    return d;
  }

  class ReelView {
    /**
     * @param {HTMLElement} el     .reel 要素 (高さ = --koma × 3、overflow hidden)
     * @param {HTMLElement} strip  .strip 要素 (絶対配置のコマを並べる帯)
     * @param {string[]}    tape   図柄配列 (index = コマ番号 = 主制御の reelPos)
     */
    constructor(el, strip, tape) {
      this.el = el; this.strip = strip; this.tape = tape; this.koma = tape.length;
      this.pos = 0;
      this.build(); this.render();
    }
    /** 21コマ + 継ぎ目用の複製2コマずつ (計 koma+5 要素) を絶対配置で並べる */
    build() {
      const K = this.koma;
      this.strip.innerHTML = "";
      for (let i = -2; i <= K + 2; i++) {
        const sym = this.tape[((i % K) + K) % K];
        const k = document.createElement("div");
        k.className = "koma";
        k.dataset.pos = String(((i % K) + K) % K);
        k.style.top = `calc(var(--koma) * ${-i})`;
        k.innerHTML = root.SlotSymbols.use(sym, "sym");
        this.strip.appendChild(k);
      }
    }
    komaPx() { return this.el.clientHeight / 3; }
    /** this.pos のコマが中段に来るよう帯を移動する */
    render() {
      const K = this.koma;
      const p = ((this.pos % K) + K) % K;
      this.strip.style.transform = `translateY(${(1 + p) * this.komaPx()}px)`;
    }
    setPos(p) { this.pos = ((p % this.koma) + this.koma) % this.koma; this.render(); }
    symbolAt(pos) { const K = this.koma; return this.tape[((pos % K) + K) % K]; }
  }

  /**
   * コンテナに .window > .reel×3 を作り ReelView を返す (カタログなど、静的な窓を出したいとき用)。
   * reel.html は自前のマークアップを持つのでこれは使わない。
   */
  function buildWindow(container, data, opts) {
    const o = Object.assign({ navi: true }, opts || {});
    root.SlotSymbols.install(container.ownerDocument);
    container.innerHTML = `
      <div class="unit">
        ${o.navi ? '<div class="navi"><span>1</span><span>2</span><span>3</span></div>' : ""}
        <div class="window">
          ${[0, 1, 2].map((i) => `<div class="reel" data-reel="${i}"><div class="strip"></div><div class="line"></div></div>`).join("")}
        </div>
      </div>`;
    return [...container.querySelectorAll(".reel")].map((el, i) => new ReelView(el, el.querySelector(".strip"), data.reels[i]));
  }

  /** 中段の3図柄が 神 / 赤7 で一直線なら .hit を付ける (reel.html の highlight と同じ規則) */
  function highlight(views) {
    const line = views.map((v) => v.symbolAt(v.pos));
    const hit = line.every((s) => s === line[0]) && (line[0] === "神" || line[0] === "赤7");
    views.forEach((v) => v.el.classList.toggle("hit", hit));
    return hit;
  }

  root.SlotReels = { load, ReelView, buildWindow, highlight };
})(typeof globalThis !== "undefined" ? globalThis : window);
