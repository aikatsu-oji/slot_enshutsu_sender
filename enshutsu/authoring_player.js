// 予告オーサリングの再生エンジン (globalThis.YokokuAuthoring)
//
//   オーサリングデータ (シーン JSON) を DOM へ組み立てて時刻 t で描画するだけの部品。
//   オーバーレイ (enshutsu_overlay.html) とエディタ (authoring_editor.html) の両方がこれを使う。
//   ここには「いつ再生するか」の判断を持たせない (それは副制御イベントを受ける側の仕事)。
//
//   シーン JSON の形 (version 1):
//     {
//       id: "akatsu",              // ファイル名 (yokoku/authoring/<id>.json) と同じ
//       name: "激アツ予告",
//       version: 1,
//       duration: 3.0,             // 全体の長さ(秒)
//       bind: { event:"banner", rank:"赤" } | null,   // どの副制御イベントで自動再生するか
//       tracks: [ クリップ, ... ]  // 配列の順に重なる (後ろほど手前)
//     }
//
//   クリップ (1つの素材の出し入れ):
//     { id, name, type:"text|image|video|shape|sound", src:"assets/xx.png", text:"激アツ",
//       start:0, dur:1.5,                     // シーン先頭からの開始秒と長さ
//       x:50, y:50, w:40, h:16,               // 中心座標と大きさ (ステージに対する %)
//       opacity:1, scale:1, rot:0,            // 見た目 (rot は度)
//       keys: [ { t:0, opacity:0, scale:.6 }, { t:.3, opacity:1, scale:1, ease:"outBack" } ] }
//
//   キーフレーム: t はクリップ先頭からの秒。x/y/w/h/opacity/scale/rot のうち書いたものだけを動かす
//   (書かなかったプロパティはクリップの基本値のまま)。ease は「そのキーへ向かう区間」の補間に効く。
//
//   座標系はすべてステージ (16:9) に対する % なので、OBS の解像度が変わっても見た目は変わらない。
//   文字サイズもステージ高さに対する % で、px 固定にしない (オーバーレイの --sh と同じ考え方)。

(function (root) {
  "use strict";

  const VERSION = 1;
  const CLIP_TYPES = ["text", "image", "video", "shape", "sound"];
  const VIDEO_EXT = [".mp4", ".webm", ".mov", ".m4v"];
  const SOUND_EXT = [".mp3", ".wav", ".ogg", ".m4a"];
  const IMAGE_EXT = [".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"];

  // 補間カーブ。キーフレームの ease に名前で指定する
  const EASINGS = {
    linear: (t) => t,
    in: (t) => t * t,
    out: (t) => 1 - (1 - t) * (1 - t),
    inOut: (t) => (t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2),
    inCubic: (t) => t * t * t,
    outCubic: (t) => 1 - Math.pow(1 - t, 3),
    outBack: (t) => 1 + 2.70158 * Math.pow(t - 1, 3) + 1.70158 * Math.pow(t - 1, 2),
    inBack: (t) => 2.70158 * t * t * t - 1.70158 * t * t,
    outBounce: (t) => {
      const n1 = 7.5625, d1 = 2.75;
      if (t < 1 / d1) return n1 * t * t;
      if (t < 2 / d1) return n1 * (t -= 1.5 / d1) * t + 0.75;
      if (t < 2.5 / d1) return n1 * (t -= 2.25 / d1) * t + 0.9375;
      return n1 * (t -= 2.625 / d1) * t + 0.984375;
    },
    step: () => 0,   // 次のキーの時刻まで前の値のまま (パラパラ切り替え用)
  };

  // 時間で動かせるプロパティ。キーフレームに書けるのはこれだけ
  const ANIM_PROPS = ["x", "y", "w", "h", "opacity", "scale", "rot"];

  const CLIP_DEFAULT = {
    id: "", name: "", type: "text", src: "", text: "",
    start: 0, dur: 1.0,
    x: 50, y: 50, w: 60, h: 18,
    opacity: 1, scale: 1, rot: 0,
    fit: "contain",                 // image / video の入れ方 (contain / cover / fill)
    color: "#ffffff", bg: "", stroke: "", strokeW: 0, radius: 0,
    fontSize: 9, fontWeight: 800, letter: 0.08, font: "",
    shadow: 1,                      // 文字と図形の影の強さ (0 で無し)
    blend: "normal",                // mix-blend-mode (screen で発光風になる)
    loop: false, volume: 1, muted: false,
    keys: [],
  };

  const SCENE_DEFAULT = { id: "", name: "", version: VERSION, duration: 3.0, bind: null, tracks: [] };

  const num = (v, d) => (Number.isFinite(Number(v)) ? Number(v) : d);
  const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
  const hasExt = (name, list) => list.some((e) => String(name).toLowerCase().endsWith(e));

  function guessType(src) {
    if (hasExt(src, VIDEO_EXT)) return "video";
    if (hasExt(src, SOUND_EXT)) return "sound";
    if (hasExt(src, IMAGE_EXT)) return "image";
    return "image";
  }

  let idSeq = 0;
  function newId(prefix) {
    idSeq += 1;
    return `${prefix}${Date.now().toString(36)}${idSeq.toString(36)}`;
  }

  // 外から来た JSON を既定値で埋めて安全な形にする。壊れた値は既定値へ寄せる (再生側で落ちないように)
  function normalizeClip(raw) {
    const c = { ...CLIP_DEFAULT, ...(raw && typeof raw === "object" ? raw : {}) };
    c.id = String(c.id || newId("c"));
    c.type = CLIP_TYPES.includes(c.type) ? c.type : "text";
    c.src = String(c.src || "");
    c.text = String(c.text == null ? "" : c.text);
    c.start = Math.max(0, num(c.start, 0));
    c.dur = Math.max(0.02, num(c.dur, 1));
    for (const p of ["x", "y", "w", "h"]) c[p] = num(c[p], CLIP_DEFAULT[p]);
    c.opacity = clamp(num(c.opacity, 1), 0, 1);
    c.scale = Math.max(0, num(c.scale, 1));
    c.rot = num(c.rot, 0);
    c.strokeW = Math.max(0, num(c.strokeW, 0));
    c.radius = Math.max(0, num(c.radius, 0));
    c.fontSize = clamp(num(c.fontSize, 9), 0.5, 100);
    c.fontWeight = clamp(num(c.fontWeight, 800), 100, 900);
    c.letter = num(c.letter, 0.08);
    c.shadow = clamp(num(c.shadow, 1), 0, 3);
    c.volume = clamp(num(c.volume, 1), 0, 1);
    c.loop = !!c.loop;
    c.muted = !!c.muted;
    c.keys = (Array.isArray(c.keys) ? c.keys : [])
      .map((k) => {
        const out = { t: Math.max(0, num(k && k.t, 0)), ease: (k && EASINGS[k.ease]) ? k.ease : "inOut" };
        for (const p of ANIM_PROPS) if (k && k[p] != null && Number.isFinite(Number(k[p]))) out[p] = Number(k[p]);
        return out;
      })
      .sort((a, b) => a.t - b.t);
    return c;
  }

  function normalizeScene(raw) {
    const s = { ...SCENE_DEFAULT, ...(raw && typeof raw === "object" ? raw : {}) };
    s.id = String(s.id || "");
    s.name = String(s.name || s.id || "無題の予告");
    s.version = VERSION;
    s.tracks = (Array.isArray(s.tracks) ? s.tracks : []).map(normalizeClip);
    // duration は指定が無ければクリップの終端に合わせる
    const end = s.tracks.reduce((m, c) => Math.max(m, c.start + c.dur), 0);
    s.duration = Math.max(0.1, num(s.duration, 0) || end || 1);
    s.bind = normalizeBind(s.bind);
    return s;
  }

  // 自動再生の割り当て。event は "banner"(予告バナーの代わり) / "freeze" / "gg_start" など副制御イベントの type。
  // banner のときは rank ("白/青/緑/赤/金") まで一致したシーンが選ばれる。trigger は任意 (lever / stop1..3)
  function normalizeBind(raw) {
    if (!raw || typeof raw !== "object" || !raw.event) return null;
    const b = { event: String(raw.event) };
    if (raw.rank) b.rank = String(raw.rank);
    if (raw.trigger) b.trigger = String(raw.trigger);
    return b;
  }

  function blankClip(type, patch) {
    return normalizeClip({ id: newId("c"), type: type || "text", ...(patch || {}) });
  }

  function blankScene(id) {
    return normalizeScene({
      id: id || "",
      name: "新しい予告",
      duration: 2.5,
      tracks: [
        blankClip("text", {
          name: "文字", text: "チャンス", start: 0, dur: 2.0,
          x: 50, y: 46, w: 80, h: 20, color: "#ffe27a", stroke: "#3a1500", strokeW: 0.6, fontSize: 12,
          keys: [
            { t: 0, opacity: 0, scale: 0.6 },
            { t: 0.35, opacity: 1, scale: 1, ease: "outBack" },
            { t: 1.7, opacity: 1, scale: 1, ease: "linear" },
            { t: 2.0, opacity: 0, scale: 1.1, ease: "in" },
          ],
        }),
      ],
    });
  }

  /* ===================== 描画 ===================== */

  const STYLE_ID = "yokoku-authoring-style";
  const STYLE_TEXT = `
.yk-stage{position:absolute;inset:0;overflow:hidden;pointer-events:none;}
.yk-clip{position:absolute;display:none;transform-origin:center center;will-change:transform,opacity;}
.yk-clip>img,.yk-clip>video{width:100%;height:100%;display:block;}
.yk-text{display:flex;align-items:center;justify-content:center;text-align:center;line-height:1.1;white-space:pre-wrap;
  font-family:"Yu Gothic","Hiragino Sans",sans-serif;}
`;

  function ensureStyle(doc) {
    const d = doc || document;
    if (d.getElementById(STYLE_ID)) return;
    const st = d.createElement("style");
    st.id = STYLE_ID;
    st.textContent = STYLE_TEXT;
    (d.head || d.documentElement).appendChild(st);
  }

  // キーフレーム補間。prop を書いたキーだけを見て、local 秒での値を返す (無ければ基本値)
  function valueAt(clip, prop, local) {
    const keys = clip.keys;
    let prev = null, next = null;
    for (const k of keys) {
      if (k[prop] == null) continue;
      if (k.t <= local) prev = k;
      else { next = k; break; }
    }
    if (!prev && !next) return clip[prop];
    if (!prev) return next[prop];              // 最初のキーより前は最初の値で待つ
    if (!next) return prev[prop];              // 最後のキーより後はその値のまま
    const span = next.t - prev.t;
    if (span <= 0) return next[prop];
    const ease = EASINGS[next.ease] || EASINGS.inOut;
    const r = ease(clamp((local - prev.t) / span, 0, 1));
    return prev[prop] + (next[prop] - prev[prop]) * r;
  }

  function propsAt(clip, local) {
    const p = {};
    for (const k of ANIM_PROPS) p[k] = valueAt(clip, k, local);
    return p;
  }

  /* 1シーンぶんの再生機。root (position:relative/absolute な箱) の中に自前のステージを作る。
     opts:
       base   … src の前に付ける URL (既定 "yokoku/authoring/")
       volume … 音量の親玉 (0〜1)。クリップの volume に掛ける
       speed  … 再生速度を返す関数 (オーバーレイのスロー再生 speedFactor をそのまま渡す)
       silent … true ならスクラブ中 (seek / renderAt) は鳴らさない。再生 (play) 中は鳴る。エディタ用 */
  function ScenePlayer(root, opts) {
    const o = opts || {};
    this.root = root;
    this.base = o.base != null ? String(o.base) : "yokoku/authoring/";
    this.getVolume = typeof o.volume === "function" ? o.volume : () => num(o.volume, 1);
    this.getSpeed = typeof o.speed === "function" ? o.speed : () => num(o.speed, 1);
    this.silent = !!o.silent;
    this.scene = null;
    this.entries = [];        // { clip, el, media }
    this.time = 0;
    this.playing = false;
    this._raf = 0;
    this._iv = 0;
    this._last = 0;
    this._resolve = null;
    this._endPromise = null;

    ensureStyle(root.ownerDocument);
    this.stage = root.ownerDocument.createElement("div");
    this.stage.className = "yk-stage";
    root.appendChild(this.stage);

    // 文字サイズをステージ高さの % で指定するため、実寸を CSS 変数に流し込む
    const sync = () => {
      const r = this.stage.getBoundingClientRect();
      this.stage.style.setProperty("--yk-h", (r.height || 0) + "px");
      this.stage.style.setProperty("--yk-w", (r.width || 0) + "px");
    };
    sync();
    if (typeof ResizeObserver === "function") {
      this._ro = new ResizeObserver(sync);
      this._ro.observe(this.stage);
    } else {
      this._onResize = sync;
      (root.ownerDocument.defaultView || window).addEventListener("resize", sync);
    }
  }

  ScenePlayer.prototype.assetUrl = function (src) {
    const s = String(src || "");
    if (!s) return "";
    if (/^(https?:|data:|blob:|\/)/.test(s)) return s;
    return this.base + s.split("/").map(encodeURIComponent).join("/");
  };

  // シーンを DOM へ組み立てる。再生位置は 0 に戻る
  ScenePlayer.prototype.load = function (scene) {
    this.stopMedia();
    this.stage.textContent = "";
    this.entries = [];
    this.scene = normalizeScene(scene);
    const doc = this.root.ownerDocument;
    for (const clip of this.scene.tracks) {
      const el = doc.createElement("div");
      el.className = "yk-clip";
      el.dataset.clipId = clip.id;
      let media = null;
      if (clip.type === "text") {
        el.classList.add("yk-text");
      } else if (clip.type === "image") {
        media = doc.createElement("img");
        media.alt = "";
        if (clip.src) media.src = this.assetUrl(clip.src);
        el.appendChild(media);
      } else if (clip.type === "video") {
        media = doc.createElement("video");
        media.playsInline = true;
        media.preload = "auto";
        media.loop = !!clip.loop;
        if (clip.src) media.src = this.assetUrl(clip.src);
        el.appendChild(media);
      } else if (clip.type === "sound") {
        media = doc.createElement("audio");
        media.preload = "auto";
        media.loop = !!clip.loop;
        if (clip.src) media.src = this.assetUrl(clip.src);
        el.appendChild(media);
      }
      this.stage.appendChild(el);
      this.entries.push({ clip, el, media, fired: false });
      this.applyStatic(this.entries[this.entries.length - 1]);
    }
    this.time = 0;
    this.renderAt(0);
    return this;
  };

  // 時間で変わらない見た目 (色・字体・角丸など) を一度だけ当てる
  ScenePlayer.prototype.applyStatic = function (entry) {
    const c = entry.clip, el = entry.el, s = el.style;
    s.mixBlendMode = c.blend || "normal";
    s.borderRadius = c.radius ? c.radius + "%" : "";
    if (c.type === "text") {
      el.textContent = c.text;
      s.color = c.color || "#fff";
      s.background = c.bg || "";
      s.fontWeight = String(c.fontWeight);
      s.letterSpacing = c.letter + "em";
      s.fontSize = `calc(var(--yk-h) * ${c.fontSize / 100})`;
      if (c.font) s.fontFamily = c.font;
      s.webkitTextStroke = c.strokeW > 0 ? `calc(var(--yk-h) * ${c.strokeW / 100}) ${c.stroke || "#000"}` : "";
      s.textShadow = c.shadow > 0
        ? `0 calc(var(--yk-h) * ${0.004 * c.shadow}) calc(var(--yk-h) * ${0.012 * c.shadow}) rgba(0,0,0,${0.55 * c.shadow})`
        : "";
    } else if (c.type === "shape") {
      el.textContent = "";
      s.background = c.bg || c.color || "#ffffff";
      s.border = c.strokeW > 0 ? `calc(var(--yk-h) * ${c.strokeW / 100}) solid ${c.stroke || "#000"}` : "";
      s.boxShadow = c.shadow > 0 ? `0 0 calc(var(--yk-h) * ${0.02 * c.shadow}) rgba(0,0,0,${0.5 * c.shadow})` : "";
    } else if (c.type === "sound") {
      s.display = "none";
    }
    if (entry.media && (c.type === "image" || c.type === "video")) {
      entry.media.style.objectFit = c.fit === "fill" ? "fill" : (c.fit === "cover" ? "cover" : "contain");
      entry.media.style.borderRadius = s.borderRadius;
    }
    if (entry.media && (c.type === "video" || c.type === "sound")) {
      entry.media.muted = !!c.muted;   // スクラブ中の消音は syncMedia が毎フレーム面倒を見る
      entry.media.volume = clamp(c.volume * this.getVolume(), 0, 1);
    }
  };

  // 指定時刻の姿を描く。再生していなくても呼べる (エディタのスクラブはこれだけを使う)
  ScenePlayer.prototype.renderAt = function (t, opts) {
    if (!this.scene) return;
    const live = !!(opts && opts.live);      // live=true のときだけ音と動画を動かす
    this.time = t;
    for (const entry of this.entries) {
      const c = entry.clip;
      const local = t - c.start;
      const active = local >= 0 && local <= c.dur;
      if (!active) {
        if (entry.el.style.display !== "none") entry.el.style.display = "none";
        if (entry.media && entry.media.pause && !entry.media.paused) entry.media.pause();
        if (local < 0) entry.fired = false;   // 巻き戻したら次の通過でまた鳴らす
        continue;
      }
      const p = propsAt(c, local);
      const s = entry.el.style;
      if (c.type !== "sound") {
        s.display = c.type === "text" ? "flex" : "block";
        s.left = (p.x - p.w / 2) + "%";
        s.top = (p.y - p.h / 2) + "%";
        s.width = p.w + "%";
        s.height = p.h + "%";
        s.opacity = String(clamp(p.opacity, 0, 1));
        s.transform = `scale(${p.scale}) rotate(${p.rot}deg)`;
      }
      if (entry.media && (c.type === "video" || c.type === "sound")) {
        this.syncMedia(entry, local, live);
      }
    }
  };

  // 動画・音声の再生位置を合わせる。live (再生中) のときだけ実際に鳴らし、スクラブ中は止めて位置だけ合わせる。
  // silent はスクラブ中の消音にしか効かない (再生中は silent でも鳴る = エディタの試聴)
  ScenePlayer.prototype.syncMedia = function (entry, local, live) {
    const m = entry.media, c = entry.clip;
    if (!m || !m.src) return;
    m.volume = clamp(c.volume * this.getVolume(), 0, 1);
    m.muted = !!c.muted || (this.silent && !live);
    const rate = clamp(this.getSpeed(), 0.25, 4);
    if (m.playbackRate !== rate) { try { m.playbackRate = rate; } catch (e) { /* 一部ブラウザは範囲外で例外 */ } }
    if (!live) {
      if (!m.paused) m.pause();
      if (c.type === "video" && Number.isFinite(m.duration)) {
        const want = c.loop && m.duration > 0 ? local % m.duration : Math.min(local, m.duration);
        if (Math.abs(m.currentTime - want) > 0.05) { try { m.currentTime = want; } catch (e) { /* まだ読めていない */ } }
      }
      return;
    }
    if (!entry.fired) {
      entry.fired = true;
      // クリップの途中から再生を始めたときも、素材の頭からではなくその位置から鳴らす
      try { m.currentTime = c.loop && m.duration > 0 ? local % m.duration : local; } catch (e) { /* まだ読めていない */ }
      playMedia(m);
    } else if (m.paused) {
      playMedia(m);
    }
  };

  // 音付きの自動再生はブラウザに拒否されることがある (ユーザー操作を挟んでいないページ)。
  // そのときは無音にして再生だけは続ける (オーバーレイの startVideo と同じ考え方)
  function playMedia(m) {
    const pr = m.play();
    if (!pr || !pr.catch) return;
    pr.catch(() => {
      if (m.muted) return;
      m.muted = true;
      const retry = m.play();
      if (retry && retry.catch) retry.catch(() => {});
    });
  }

  ScenePlayer.prototype.stopMedia = function () {
    for (const entry of this.entries) {
      entry.fired = false;
      if (entry.media && entry.media.pause) {
        try { entry.media.pause(); entry.media.currentTime = 0; } catch (e) { /* 未読込 */ }
      }
    }
  };

  Object.defineProperty(ScenePlayer.prototype, "duration", {
    get: function () { return this.scene ? this.scene.duration : 0; },
  });

  // 先頭 (または from 秒) から再生する。戻り値は再生し終わったときに解決する Promise。
  // 再生中にもう一度呼ぶと、前の再生は打ち切って (Promise は解決して) 最初から流し直す
  ScenePlayer.prototype.play = function (from) {
    this.stopMedia();
    this.finish();
    this.playing = true;
    this.time = Math.max(0, num(from, 0));
    this._last = performance.now();
    const win = this.root.ownerDocument.defaultView || window;
    this._endPromise = new Promise((resolve) => { this._resolve = resolve; });
    // 進行は requestAnimationFrame。ただし見えていないタブ(OBS の裏、別タブ)では rAF が止まるので、
    // 保険のタイマーからも同じ tick を叩く。これが無いと再生が途中で固まったまま終わらない
    const tick = () => {
      if (!this.playing) return;
      const now = performance.now();
      const dt = (now - this._last) / 1000 * clamp(this.getSpeed(), 0.05, 8);
      this._last = now;
      this.time += dt;
      if (this.time >= this.duration) {
        this.renderAt(this.duration, { live: true });
        this.stop();
        return;
      }
      this.renderAt(this.time, { live: true });
      if (this._raf) win.cancelAnimationFrame(this._raf);
      this._raf = win.requestAnimationFrame(tick);
    };
    this.renderAt(this.time, { live: true });
    this._raf = win.requestAnimationFrame(tick);
    this._iv = win.setInterval(tick, 100);
    return this._endPromise;
  };

  // 再生を止めて全クリップを隠す。待っている Promise は解決する (呼び出し側は必ず先へ進む)
  ScenePlayer.prototype.stop = function () {
    const win = this.root.ownerDocument.defaultView || window;
    if (this._raf) { win.cancelAnimationFrame(this._raf); this._raf = 0; }
    if (this._iv) { win.clearInterval(this._iv); this._iv = 0; }
    this.playing = false;
    this.stopMedia();
    for (const entry of this.entries) entry.el.style.display = "none";
    this.finish();
  };

  ScenePlayer.prototype.finish = function () {
    const r = this._resolve;
    this._resolve = null;
    this._endPromise = null;
    if (r) r();
  };

  // エディタのスクラブ用。音は出さずにその時刻の姿だけを描く
  ScenePlayer.prototype.seek = function (t) {
    const win = this.root.ownerDocument.defaultView || window;
    if (this._raf) { win.cancelAnimationFrame(this._raf); this._raf = 0; }
    if (this._iv) { win.clearInterval(this._iv); this._iv = 0; }
    this.playing = false;
    this.finish();
    this.renderAt(clamp(num(t, 0), 0, this.duration), { live: false });
  };

  ScenePlayer.prototype.destroy = function () {
    this.stop();
    if (this._ro) this._ro.disconnect();
    if (this._onResize) (this.root.ownerDocument.defaultView || window).removeEventListener("resize", this._onResize);
    if (this.stage.parentNode) this.stage.parentNode.removeChild(this.stage);
    this.entries = [];
    this.scene = null;
  };

  /* ===================== 割り当ての照合 =====================
     副制御イベント (banner / lever / stop / freeze など) に対して、bind が一致するシーンを選ぶ。
     bind.rank / bind.trigger は書いてあるときだけ見る (書いていなければどのランクでも一致)。 */
  function matchScene(scenes, ev) {
    if (!ev || !Array.isArray(scenes)) return null;
    // lever / stop は「バナーを出すイベント」なので banner として扱う
    const kind = (ev.type === "lever" || ev.type === "stop" || ev.type === "banner") ? "banner" : String(ev.type || "");
    const trigger = ev.type === "stop" ? "stop" + ev.n : (ev.type === "lever" ? "lever" : (ev.trigger || ""));
    let best = null, bestScore = -1;
    for (const s of scenes) {
      const b = s && s.bind;
      if (!b || b.event !== kind) continue;
      if (b.rank && String(b.rank) !== String(ev.rank || "")) continue;
      if (b.trigger && String(b.trigger) !== String(trigger)) continue;
      const score = (b.rank ? 2 : 0) + (b.trigger ? 1 : 0);   // 条件が細かいものを優先する
      if (score > bestScore) { best = s; bestScore = score; }
    }
    return best;
  }

  root.YokokuAuthoring = {
    VERSION, CLIP_TYPES, EASINGS, ANIM_PROPS, CLIP_DEFAULT, SCENE_DEFAULT,
    VIDEO_EXT, SOUND_EXT, IMAGE_EXT,
    ScenePlayer, normalizeScene, normalizeClip, normalizeBind,
    blankScene, blankClip, guessType, matchScene, valueAt, propsAt, newId,
  };
})(typeof globalThis !== "undefined" ? globalThis : window);
