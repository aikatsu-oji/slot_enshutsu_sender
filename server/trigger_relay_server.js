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
//   3. enshutsu\enshutsu_overlay.html をOBSのブラウザソースとして開く(自動で接続します)
//   4. main_control.html をブラウザで開く(自動で接続します)
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

const PORT = 8787;
const wss = new WebSocketServer({ port: PORT });

console.log(`[演出トリガー中継サーバー] ws://localhost:${PORT} で待機中...`);
console.log("このウィンドウは起動したまま(閉じない)にしておいてください。");

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
