// 演出トリガー用の軽量WebSocket中継サーバー
//
// 配置: (プロジェクトルート)\server\trigger_relay_server.js
//   ※ node_modules はプロジェクトルート直下に置く想定です。
//     Node はファイルの場所から上の階層へ順に node_modules を探すため、
//     server\ 配下からでもルートの ws パッケージをそのまま読み込めます。
//
// 使い方:
//   1. npm install ws                      ※プロジェクトルートで実行。setup.bat を使えば自動で行われます
//   2. node server\trigger_relay_server.js ※プロジェクトルートで実行。setup.bat を使えば自動で起動します
//   3. OBSのブラウザソースに http://localhost:8787/enshutsu/enshutsu_overlay.html を指定する
//   4. http://localhost:8787/control/main_control.html をブラウザで開く(自動で接続します)
//      ※ 旧URL http://localhost:8787/main_control.html は新URLへ自動転送します
//
//   コマンドラインからの起動/停止は scripts\dev.cmd (Claude Code 向け) を参照。
//   ポートは環境変数 PORT で変更できます(既定 8787)。
//
//   ※ このサーバーはWebSocket中継に加えて、プロジェクトルートを静的配信します。
//     http:// 経由で開いた enshutsu_overlay.html は cutin / freeze フォルダを
//     /api/list 経由で自動的に読み込むため、フォルダ選択も権限の再許可も不要になります。
//     ファイルを直接ダブルクリックして file:// で開いた場合は、
//     従来どおり File System Access API でフォルダを選択する動作になります。
//
//   main_control.html のボタン操作、または他の演出制御HTML/スクリプトから
//   以下のようなJSONを送信すると、enshutsu_overlay.html側の演出が呼び出されます。
//
//      ws.send(JSON.stringify({ action: "playAuthoring", id: "akatsu" }));
//
//   action に指定できる値:
//     "toggleSlow"                                   … スロー再生の切り替え
//     "toggleSettings"                               … 設定パネルの開閉
//     "reelIn" / "reelOut" / "reelToggle"            … リールユニット(筐体ビュー)を液晶内に入れる / 出す / 切替
//     "subEvent" (event: {...})                      … 副制御の演出イベントをオーバーレイへ直接流す(確認用)
//                                                      例: {"action":"subEvent","event":{"type":"banner","rank":"赤"}}
//     "playAuthoring" (id: "...")                    … 予告オーサリングのシーンを1回再生する
//                                                      例: {"action":"playAuthoring","id":"akatsu"}
//
// このサーバーはメッセージを「受け取ったら他の全クライアントに転送するだけ」の単純な中継役です。

const { WebSocketServer } = require("ws");
const http = require("http");
const fs = require("fs");
const path = require("path");

const PORT = Number(process.env.PORT) || 8787;

// 旧配置(ルート直下)のURLを新配置へ転送する。ブックマークやOBSの設定を壊さないための互換措置。
const LEGACY_REDIRECTS = {
  "/main_control.html": "/control/main_control.html",
  "/kyotai.html": "/reel/reel.html",
  "/kyotai/kyotai.html": "/reel/reel.html",
};

// ===================== 静的配信 + フォルダ一覧API =====================
// プロジェクトルート(このファイルの1つ上)を公開ルートとして配信します。
// これにより enshutsu_overlay.html を http:// 経由で開けるようになり、
// 画像フォルダを File System Access API で選ばせる必要がなくなります。
const ROOT = path.resolve(__dirname, "..");

const MIME = {
  ".html": "text/html; charset=utf-8",
  ".htm": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".gif": "image/gif",
  ".webp": "image/webp",
  ".svg": "image/svg+xml",
  ".ico": "image/x-icon",
  ".mp3": "audio/mpeg",
  ".wav": "audio/wav",
  ".ogg": "audio/ogg",
  ".m4a": "audio/mp4",
  ".mp4": "video/mp4",
  ".webm": "video/webm",
  ".woff": "font/woff",
  ".woff2": "font/woff2",
  ".ttf": "font/ttf",
};

// URLパスを ROOT 配下の実パスに変換する。ROOT の外へ出ようとする指定は null を返す。
function resolveSafe(urlPath) {
  let decoded;
  try {
    decoded = decodeURIComponent(urlPath);
  } catch (e) {
    return null;
  }
  const normalized = path.posix.normalize("/" + decoded.replace(/\\/g, "/"));
  const full = path.resolve(ROOT, "." + normalized);
  if (full !== ROOT && !full.startsWith(ROOT + path.sep)) return null;
  return full;
}

function sendJson(res, status, payload) {
  const body = JSON.stringify(payload);
  res.writeHead(status, {
    "Content-Type": "application/json; charset=utf-8",
    "Cache-Control": "no-store",
  });
  res.end(body);
}

// GET /api/list?dir=/enshutsu/cutin
//   → { dir: "...", files: ["01_はな.png", "02_ゆき.png", ...] }
// 指定フォルダ直下のファイル名だけを名前順(数字を数値として比較)で返します。
function handleApiList(res, url) {
  const dir = url.searchParams.get("dir") || "/";
  const full = resolveSafe(dir);
  if (!full) {
    sendJson(res, 400, { error: "invalid dir", files: [] });
    return;
  }
  fs.readdir(full, { withFileTypes: true }, (err, entries) => {
    if (err) {
      // フォルダが存在しない場合も、空配列を返してオーバーレイ側を止めないようにする
      sendJson(res, 200, { dir, files: [], missing: true });
      return;
    }
    const files = entries
      .filter((e) => e.isFile())
      .map((e) => e.name)
      .sort((a, b) => a.localeCompare(b, "ja", { numeric: true }));
    sendJson(res, 200, { dir, files });
  });
}

// ファイル1件を配信する。動画(mp4/webm)の再生・シーク・巻き戻しに必要な Range リクエスト(206)と、
// 大きな素材を毎回ダウンロードし直さないための Last-Modified / 304 に対応する。
// Cache-Control: no-cache は「使う前に必ず再検証する」の意味なので、素材を差し替えれば即座に反映される。
function serveFile(req, res, target) {
  fs.stat(target, (err, st) => {
    if (err) {
      res.writeHead(404, { "Content-Type": "text/plain; charset=utf-8" });
      res.end("404 Not Found");
      return;
    }
    const type = MIME[path.extname(target).toLowerCase()] || "application/octet-stream";
    const size = st.size;
    const lastModified = st.mtime.toUTCString();
    const baseHeaders = {
      "Content-Type": type,
      "Cache-Control": "no-cache",
      "Last-Modified": lastModified,
      "Accept-Ranges": "bytes",
    };

    const since = req.headers["if-modified-since"];
    if (since && !req.headers.range) {
      const sinceMs = Date.parse(since);
      if (!Number.isNaN(sinceMs) && Math.floor(st.mtimeMs / 1000) * 1000 <= sinceMs) {
        res.writeHead(304, baseHeaders);
        res.end();
        return;
      }
    }

    const range = req.headers.range;
    const m = range && /^bytes=(\d*)-(\d*)$/.exec(range);
    if (m && (m[1] !== "" || m[2] !== "")) {
      let start, end;
      if (m[1] === "") {
        // bytes=-N … 末尾N バイト
        const suffix = parseInt(m[2], 10);
        start = Math.max(0, size - suffix);
        end = size - 1;
      } else {
        start = parseInt(m[1], 10);
        end = m[2] === "" ? size - 1 : Math.min(parseInt(m[2], 10), size - 1);
      }
      if (Number.isNaN(start) || Number.isNaN(end) || start > end || start >= size) {
        res.writeHead(416, { "Content-Range": `bytes */${size}` });
        res.end();
        return;
      }
      res.writeHead(206, {
        ...baseHeaders,
        "Content-Length": end - start + 1,
        "Content-Range": `bytes ${start}-${end}/${size}`,
      });
      if (req.method === "HEAD") { res.end(); return; }
      fs.createReadStream(target, { start, end }).pipe(res);
      return;
    }

    res.writeHead(200, { ...baseHeaders, "Content-Length": size });
    if (req.method === "HEAD") { res.end(); return; }
    fs.createReadStream(target).pipe(res);
  });
}

// ===================== 図柄設定の保存 API =====================
// POST /api/symbols  (reel/symbol_editor.html から)
//   body: { settings: {reelBg, symW, symH},
//           images: { <id>: { png: "data:image/png;base64,...",   ← reel/img/<id>.png に保存 (元画像・切り抜き済み)
//                             src: "data:image/webp;base64,...",  ← symbol_images.js に入れる表示用 (縮小版)
//                             w, h, scale, dx, dy } | null } }   ← null は画像を消してベクター描画に戻す
//   images に無い id は現状維持。保存後 { action: "symbolsUpdated" } を全 WebSocket クライアントへ流す
//   (筐体ビューはこれを受けて再読込する)。
const SYMBOL_IDS = ["god", "seven", "bell", "rep", "melon", "blank"];
const SYMBOL_IMG_DIR = path.join(ROOT, "reel", "img");
const SYMBOL_IMAGES_JS = path.join(ROOT, "reel", "symbol_images.js");
const SYMBOL_DEFAULT_SETTINGS = {
  reelBg: "#fdfbf3", reelBgAlpha: 1, reelBgImage: "", reelBgImageAlpha: 1, reelBgFit: "cover",
  frameColor: "#c9a24a", frameAlpha: 1, framePad: 6, frameGap: 6, frameRadius: 0, shade: 0.45, lineAlpha: 1,
  symW: 82, symH: 74,
};
const REEL_BG_PNG = path.join(SYMBOL_IMG_DIR, "reel_bg.png");   // リール背景画像の元 (settings.reelBgImage は表示用の縮小版)

function loadSymbolImages() {
  try {
    const sandbox = {};
    require("vm").runInNewContext(fs.readFileSync(SYMBOL_IMAGES_JS, "utf-8"), { globalThis: sandbox });
    const d = sandbox.SlotSymbolImages || {};
    return { settings: { ...SYMBOL_DEFAULT_SETTINGS, ...(d.settings || {}) }, images: { ...(d.images || {}) } };
  } catch (e) {
    return { settings: { ...SYMBOL_DEFAULT_SETTINGS }, images: {} };
  }
}

function saveSymbolImages(store) {
  const num = (v, d) => (Number.isFinite(Number(v)) ? Number(v) : d);
  const lines = SYMBOL_IDS.filter((k) => store.images[k] && store.images[k].src).map((k) => {
    const m = store.images[k];
    return `      ${k}: { w: ${num(m.w, 0)}, h: ${num(m.h, 0)}, scale: ${num(m.scale, 1)}, dx: ${num(m.dx, 0)}, dy: ${num(m.dy, 0)}, src: ${JSON.stringify(String(m.src))} },`;
  });
  const s = store.settings;
  const settings = {
    reelBg: /^#[0-9a-fA-F]{6}$/.test(String(s.reelBg)) ? String(s.reelBg).toLowerCase() : SYMBOL_DEFAULT_SETTINGS.reelBg,
    reelBgAlpha: Math.min(1, Math.max(0, num(s.reelBgAlpha, 1))),
    reelBgImage: dataUriToBuffer(s.reelBgImage) ? String(s.reelBgImage) : "",
    reelBgImageAlpha: Math.min(1, Math.max(0, num(s.reelBgImageAlpha, 1))),
    reelBgFit: ["cover", "contain", "stretch", "tile"].includes(s.reelBgFit) ? s.reelBgFit : "cover",
    frameColor: /^#[0-9a-fA-F]{6}$/.test(String(s.frameColor)) ? String(s.frameColor).toLowerCase() : SYMBOL_DEFAULT_SETTINGS.frameColor,
    frameAlpha: Math.min(1, Math.max(0, num(s.frameAlpha, 1))),
    framePad: Math.min(200, Math.max(0, num(s.framePad, 6))),
    frameGap: Math.min(200, Math.max(0, num(s.frameGap, 6))),
    frameRadius: Math.min(200, Math.max(0, num(s.frameRadius, 0))),
    shade: Math.min(1, Math.max(0, num(s.shade, 0.45))),
    lineAlpha: Math.min(1, Math.max(0, num(s.lineAlpha, 1))),
    symW: Math.min(100, Math.max(10, num(s.symW, SYMBOL_DEFAULT_SETTINGS.symW))),
    symH: Math.min(100, Math.max(10, num(s.symH, SYMBOL_DEFAULT_SETTINGS.symH))),
  };
  const out =
    "// 画像図柄と設定 (data URI)。symbols.js より先に読み込む。図柄設定 symbol_editor.html から保存される (手で編集しない)\n" +
    "//   元画像は reel/img/<id>.png。images の w/h は data URI の画素数、scale/dx/dy は表示時の拡大率と位置 (viewBox 240×80 基準)\n" +
    "(function (root) {\n  root.SlotSymbolImages = {\n" +
    `    settings: ${JSON.stringify(settings)},\n` +
    "    images: {\n" + lines.join("\n") + (lines.length ? "\n" : "") + "    },\n  };\n" +
    '})(typeof globalThis !== "undefined" ? globalThis : window);\n';
  fs.writeFileSync(SYMBOL_IMAGES_JS, out);
  return settings;
}

function dataUriToBuffer(s, type) {
  const m = /^data:([^;,]+);base64,(.+)$/s.exec(String(s || ""));
  if (!m || (type && m[1] !== type)) return null;
  return Buffer.from(m[2], "base64");
}

function handleApiSymbols(req, res) {
  if (req.method !== "POST") { sendJson(res, 405, { error: "POST only" }); return; }
  const chunks = [];
  let size = 0;
  req.on("data", (c) => { size += c.length; if (size > 64 * 1024 * 1024) { req.destroy(); } else chunks.push(c); });
  req.on("end", () => {
    let body;
    try { body = JSON.parse(Buffer.concat(chunks).toString("utf-8")); }
    catch (e) { sendJson(res, 400, { error: "invalid json" }); return; }
    const store = loadSymbolImages();
    if (body.settings && typeof body.settings === "object") {
      const { reelBgImage, ...rest } = body.settings;   // 背景画像は reelImage で受ける (settings 側の値は無視)
      Object.assign(store.settings, rest);
    }
    const written = [], removed = [];
    fs.mkdirSync(SYMBOL_IMG_DIR, { recursive: true });
    // リール背景画像: reelImage = null で削除、{ png, src } で差し替え、{ src } だけなら表示用の差し替え (不透明度の焼き直し)、無ければ現状維持
    if (body.reelImage === null) {
      store.settings.reelBgImage = "";
      try { fs.unlinkSync(REEL_BG_PNG); } catch (e) { /* 無ければ何もしない */ }
      removed.push("reel_bg");
    } else if (body.reelImage && typeof body.reelImage === "object") {
      const png = body.reelImage.png != null ? dataUriToBuffer(body.reelImage.png, "image/png") : undefined;
      if (png === null || !dataUriToBuffer(body.reelImage.src)) { sendJson(res, 400, { error: "reelImage: png (省略可) と src を data URI で送ってください" }); return; }
      if (png) { fs.writeFileSync(REEL_BG_PNG, png); written.push("reel_bg"); }
      store.settings.reelBgImage = String(body.reelImage.src);
    }
    for (const [id, m] of Object.entries(body.images || {})) {
      if (!SYMBOL_IDS.includes(id)) { sendJson(res, 400, { error: "unknown symbol id: " + id }); return; }
      const pngPath = path.join(SYMBOL_IMG_DIR, id + ".png");
      if (m === null) {
        delete store.images[id];
        try { fs.unlinkSync(pngPath); } catch (e) { /* 無ければ何もしない */ }
        removed.push(id);
        continue;
      }
      if (m.png) {
        const png = dataUriToBuffer(m.png, "image/png");
        if (!png) { sendJson(res, 400, { error: id + ": png は data:image/png;base64 で送ってください" }); return; }
        fs.writeFileSync(pngPath, png);
      }
      const src = m.src || (store.images[id] && store.images[id].src);
      if (!src || !dataUriToBuffer(src)) { sendJson(res, 400, { error: id + ": src (表示用 data URI) がありません" }); return; }
      store.images[id] = { w: m.w, h: m.h, scale: m.scale, dx: m.dx, dy: m.dy, src };
      written.push(id);
    }
    const settings = saveSymbolImages(store);
    const msg = JSON.stringify({ action: "symbolsUpdated", written, removed });
    for (const client of wss.clients) if (client.readyState === client.OPEN) client.send(msg);
    console.log("[図柄設定] 保存", { written, removed, settings });
    sendJson(res, 200, { ok: true, written, removed, settings, images: Object.keys(store.images) });
  });
}

// ===================== 予告オーサリングの保存 API =====================
// シーン (オーサリングデータ) は enshutsu/yokoku/authoring/<id>.json、素材は同フォルダの assets/ に置く。
//   GET  /api/authoring          → { scenes: [シーンJSON, ...] }  (オーバーレイが起動時とid更新時に読む)
//   GET  /api/authoring?id=xxx   → シーンJSON 1件
//   POST /api/authoring          body: { id, scene } で保存 / { id, delete:true } で削除
//   POST /api/authoring/asset    body: { name, data:"data:...;base64,..." } で素材を1件保存
// 保存・削除のあとは { action:"authoringUpdated", id, op } を全 WebSocket クライアントへ流す
// (オーバーレイはこれを受けてシーンを読み直す)。
const AUTHORING_DIR = path.join(ROOT, "enshutsu", "yokoku", "authoring");
const AUTHORING_ASSET_DIR = path.join(AUTHORING_DIR, "assets");
const AUTHORING_ASSET_EXT = [".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg", ".mp4", ".webm", ".mov", ".m4v", ".mp3", ".wav", ".ogg", ".m4a"];

// ファイル名に使えない文字と、上の階層へ出る指定を弾く。ドットを禁じているので "..", "a.json" も通らない
function safeSceneId(raw) {
  const s = String(raw == null ? "" : raw).trim();
  if (!s || s.length > 48) return null;
  if (/[\\/:*?"<>|.\u0000-\u001f]/.test(s)) return null;
  return s;
}

function safeAssetName(raw) {
  const s = path.basename(String(raw == null ? "" : raw).trim()).replace(/[\\/:*?"<>|\u0000-\u001f]/g, "_");
  if (!s || s.length > 96 || s.startsWith(".")) return null;
  const ext = path.extname(s).toLowerCase();
  if (!AUTHORING_ASSET_EXT.includes(ext)) return null;
  return s;
}

function readScene(id) {
  try {
    const scene = JSON.parse(fs.readFileSync(path.join(AUTHORING_DIR, id + ".json"), "utf-8"));
    if (!scene || typeof scene !== "object") return null;
    scene.id = id;
    return scene;
  } catch (e) {
    return null;
  }
}

function listScenes() {
  let names = [];
  try { names = fs.readdirSync(AUTHORING_DIR).filter((n) => n.toLowerCase().endsWith(".json")); }
  catch (e) { return []; }
  names.sort((a, b) => a.localeCompare(b, "ja", { numeric: true }));
  return names.map((n) => readScene(n.replace(/\.json$/i, ""))).filter(Boolean);
}

function broadcast(payload) {
  const msg = JSON.stringify(payload);
  for (const client of wss.clients) if (client.readyState === client.OPEN) client.send(msg);
}

// リクエストのボディを JSON として読む (上限つき)。読めなければ res へエラーを返して null を渡す
function readJsonBody(req, res, limitBytes, cb) {
  const chunks = [];
  let size = 0;
  req.on("data", (c) => { size += c.length; if (size > limitBytes) { req.destroy(); } else chunks.push(c); });
  req.on("end", () => {
    try { cb(JSON.parse(Buffer.concat(chunks).toString("utf-8"))); }
    catch (e) { sendJson(res, 400, { error: "invalid json" }); }
  });
}

function handleApiAuthoring(req, res, url) {
  if (req.method === "GET") {
    const id = url.searchParams.get("id");
    if (id) {
      const safe = safeSceneId(id);
      const scene = safe && readScene(safe);
      if (!scene) { sendJson(res, 404, { error: "not found: " + id }); return; }
      sendJson(res, 200, { scene });
      return;
    }
    sendJson(res, 200, { scenes: listScenes() });
    return;
  }
  if (req.method !== "POST") { sendJson(res, 405, { error: "GET or POST only" }); return; }
  readJsonBody(req, res, 16 * 1024 * 1024, (body) => {
    const id = safeSceneId(body && body.id);
    if (!id) { sendJson(res, 400, { error: "id が不正です (\\ / : * ? \" < > | . は使えません。48文字まで)" }); return; }
    const file = path.join(AUTHORING_DIR, id + ".json");
    if (body.delete) {
      try { fs.unlinkSync(file); } catch (e) { sendJson(res, 404, { error: "not found: " + id }); return; }
      broadcast({ action: "authoringUpdated", id, op: "delete" });
      console.log("[予告オーサリング] 削除", id);
      sendJson(res, 200, { ok: true, id, op: "delete", scenes: listScenes().map((s) => s.id) });
      return;
    }
    const scene = body.scene;
    if (!scene || typeof scene !== "object" || !Array.isArray(scene.tracks)) {
      sendJson(res, 400, { error: "scene: tracks 配列を持つオブジェクトを送ってください" });
      return;
    }
    scene.id = id;
    fs.mkdirSync(AUTHORING_DIR, { recursive: true });
    fs.writeFileSync(file, JSON.stringify(scene, null, 2) + "\n");
    broadcast({ action: "authoringUpdated", id, op: "save" });
    console.log("[予告オーサリング] 保存", id, `(${scene.tracks.length} クリップ / ${scene.duration} 秒)`);
    sendJson(res, 200, { ok: true, id, op: "save" });
  });
}

function handleApiAuthoringAsset(req, res) {
  if (req.method === "GET") {
    let files = [];
    try {
      files = fs.readdirSync(AUTHORING_ASSET_DIR, { withFileTypes: true })
        .filter((e) => e.isFile() && AUTHORING_ASSET_EXT.includes(path.extname(e.name).toLowerCase()))
        .map((e) => e.name)
        .sort((a, b) => a.localeCompare(b, "ja", { numeric: true }));
    } catch (e) { /* フォルダが無ければ空 */ }
    sendJson(res, 200, { files });
    return;
  }
  if (req.method !== "POST") { sendJson(res, 405, { error: "GET or POST only" }); return; }
  readJsonBody(req, res, 128 * 1024 * 1024, (body) => {
    if (body && body.delete) {
      const name = safeAssetName(body.name);
      if (!name) { sendJson(res, 400, { error: "name が不正です" }); return; }
      try { fs.unlinkSync(path.join(AUTHORING_ASSET_DIR, name)); }
      catch (e) { sendJson(res, 404, { error: "not found: " + name }); return; }
      console.log("[予告オーサリング] 素材を削除", name);
      sendJson(res, 200, { ok: true, name, op: "delete" });
      return;
    }
    const name = safeAssetName(body && body.name);
    if (!name) { sendJson(res, 400, { error: "name が不正です (対応拡張子: " + AUTHORING_ASSET_EXT.join(" ") + ")" }); return; }
    const buf = dataUriToBuffer(body && body.data);
    if (!buf) { sendJson(res, 400, { error: "data は data:<mime>;base64,... で送ってください" }); return; }
    fs.mkdirSync(AUTHORING_ASSET_DIR, { recursive: true });
    fs.writeFileSync(path.join(AUTHORING_ASSET_DIR, name), buf);
    console.log("[予告オーサリング] 素材を保存", name, `(${buf.length} バイト)`);
    sendJson(res, 200, { ok: true, name, src: "assets/" + name });
  });
}

const server = http.createServer((req, res) => {
  const url = new URL(req.url, "http://localhost");

  if (url.pathname === "/api/list") {
    handleApiList(res, url);
    return;
  }

  if (url.pathname === "/api/symbols") {
    handleApiSymbols(req, res);
    return;
  }

  if (url.pathname === "/api/authoring") {
    handleApiAuthoring(req, res, url);
    return;
  }

  if (url.pathname === "/api/authoring/asset") {
    handleApiAuthoringAsset(req, res);
    return;
  }

  // 死活確認用(scripts/dev.ps1 の status や Claude Code からの疎通確認に使う)
  if (url.pathname === "/api/health") {
    sendJson(res, 200, { ok: true, port: PORT, clients: wss.clients.size, root: ROOT });
    return;
  }

  if (LEGACY_REDIRECTS[url.pathname]) {
    res.writeHead(302, { Location: LEGACY_REDIRECTS[url.pathname] + url.search });
    res.end();
    return;
  }

  const full = resolveSafe(url.pathname);
  if (!full) {
    res.writeHead(403, { "Content-Type": "text/plain; charset=utf-8" });
    res.end("403 Forbidden");
    return;
  }

  fs.stat(full, (err, stat) => {
    if (err) {
      res.writeHead(404, { "Content-Type": "text/plain; charset=utf-8" });
      res.end("404 Not Found: " + url.pathname);
      return;
    }
    const target = stat.isDirectory() ? path.join(full, "index.html") : full;
    fs.stat(target, (err2) => {
      if (err2) {
        res.writeHead(404, { "Content-Type": "text/plain; charset=utf-8" });
        res.end("404 Not Found: " + url.pathname);
        return;
      }
      serveFile(req, res, target);
    });
  });
});

// WebSocketを同じサーバー(同じポート)に相乗りさせる
const wss = new WebSocketServer({ server });

server.listen(PORT, () => {
  console.log(`[演出トリガー中継サーバー] ws://localhost:${PORT} で待機中...`);
  console.log(`[静的配信] http://localhost:${PORT}/ (公開ルート: ${ROOT})`);
  console.log(`[オーバーレイURL] http://localhost:${PORT}/enshutsu/enshutsu_overlay.html`);
  console.log(`[コンパネURL]     http://localhost:${PORT}/control/main_control.html`);
  console.log(`[筐体ビューURL]   http://localhost:${PORT}/reel/reel.html?mode=link&hidebar=1`);
  console.log(`[図柄設定URL]     http://localhost:${PORT}/reel/symbol_editor.html`);
  console.log(`[予告オーサリング] http://localhost:${PORT}/enshutsu/authoring_editor.html`);
  console.log("このウィンドウは起動したまま(閉じない)にしておいてください。");
});

wss.on("connection", (socket) => {
  console.log("[接続] クライアントが接続しました。現在の接続数:", wss.clients.size);

  socket.on("message", (raw) => {
    const text = raw.toString();

    // 届いたメッセージを、送信元以外の全クライアントに転送する
    for (const client of wss.clients) {
      if (client !== socket && client.readyState === client.OPEN) {
        client.send(text);
      }
    }

    try {
      const data = JSON.parse(text);
      console.log("[配信]", data);
    } catch (e) {
      console.log("[配信] (JSON以外のメッセージ):", text);
    }
  });

  socket.on("close", () => {
    console.log("[切断] クライアントが切断しました。現在の接続数:", wss.clients.size);
  });
});
