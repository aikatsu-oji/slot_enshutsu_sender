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
//   4. http://localhost:8787/main_control.html をブラウザで開く(自動で接続します)
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
//      ws.send(JSON.stringify({ action: "playUpToLock2" }));
//
//   action に指定できる値:
//     "playLock1" / "playLock2" / "playLock3"       … その段階だけを単体で再生
//     "playUpToLock1" / "playUpToLock2" / "playUpToLock3" … ロック1から順に再生し、指定の段階で終了
//     "triggerEnshutsu"                              … フル演出(ロック1→2→3)を1回再生
//     "toggleSlow"                                   … スロー再生の切り替え
//     "toggleSettings"                               … 設定パネルの開閉
//
// このサーバーはメッセージを「受け取ったら他の全クライアントに転送するだけ」の単純な中継役です。

const { WebSocketServer } = require("ws");
const http = require("http");
const fs = require("fs");
const path = require("path");

const PORT = 8787;

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

const server = http.createServer((req, res) => {
  const url = new URL(req.url, "http://localhost");

  if (url.pathname === "/api/list") {
    handleApiList(res, url);
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
      const type = MIME[path.extname(target).toLowerCase()] || "application/octet-stream";
      // フォルダに画像を追加・差し替えたら即座に反映されるようキャッシュを無効化
      res.writeHead(200, { "Content-Type": type, "Cache-Control": "no-cache" });
      fs.createReadStream(target).pipe(res);
    });
  });
});

// WebSocketを同じサーバー(同じポート)に相乗りさせる
const wss = new WebSocketServer({ server });

server.listen(PORT, () => {
  console.log(`[演出トリガー中継サーバー] ws://localhost:${PORT} で待機中...`);
  console.log(`[静的配信] http://localhost:${PORT}/ (公開ルート: ${ROOT})`);
  console.log(`[オーバーレイURL] http://localhost:${PORT}/enshutsu/enshutsu_overlay.html`);
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
