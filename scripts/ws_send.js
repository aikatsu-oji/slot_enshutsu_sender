// 中継サーバーへ JSON を1件だけ送って終了する小さなクライアント。
// 使い方: node scripts/ws_send.js ws://127.0.0.1:8787 '{"action":"triggerEnshutsu"}'
//   (scripts/dev.cmd send '{"action":"triggerEnshutsu"}' から呼ばれる)
// 主制御を起動せずに、コンパネのボタンと同じメッセージをコマンドラインから投げられる。
const WebSocket = require("ws");

const [, , url = "ws://127.0.0.1:8787", ...rest] = process.argv;
const text = rest.join(" ").trim();
if (!text) {
  console.error("送信する JSON を指定してください");
  process.exit(2);
}
try {
  JSON.parse(text);
} catch (e) {
  console.error("JSON として解釈できません:", e.message);
  process.exit(2);
}

const ws = new WebSocket(url);
const timer = setTimeout(() => {
  console.error("タイムアウト: 中継サーバーに接続できません", url);
  process.exit(1);
}, 3000);

ws.on("open", () => {
  ws.send(text, () => {
    console.log("[送信]", text);
    clearTimeout(timer);
    ws.close();
  });
});
ws.on("error", (e) => {
  console.error("接続エラー:", e.message);
  process.exit(1);
});
