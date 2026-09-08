// 設定の読み取りだけ。認証 (auth.js) を通らずに使えるようにここに分けてある。
//
// チャット連動モードは **認証もアプリ登録も要らない** ので、
// チャンネル名を知るためだけに auth.js (と fetch や Device Code Grant) を
// 読み込まなくて済むようにしている。
//
// 置き場所 (.gitignore 済みの .run/ 配下。無ければ無いで動く):
//   .run/twitch_config.json  { "channel": "...", "clientId": "..." }
// 環境変数 TWITCH_CHANNEL / TWITCH_CLIENT_ID があればそれが最優先。
//
// clientId は **チャット連動では使わない**。
// レイド・ビッツ・サブスクなど EventSub が要るイベントを後々足すときだけ、
// 自分で登録したアプリの ID をここに入れる (doc/twitch認証の取り方.md)。

const fs = require("fs");
const path = require("path");

const ROOT = path.resolve(__dirname, "..");
const RUN_DIR = path.join(ROOT, ".run");
const CONFIG_PATH = path.join(RUN_DIR, "twitch_config.json");

function readJson(file) {
  try {
    return JSON.parse(fs.readFileSync(file, "utf-8"));
  } catch (e) {
    return null;
  }
}

// 足りなくてもここでは投げない。何が要るかはモードによって違うので、
// 使う側 (チャット連動ならチャンネル名、EventSub なら clientId) が判断する。
function loadConfig() {
  const file = readJson(CONFIG_PATH) || {};
  return {
    clientId: process.env.TWITCH_CLIENT_ID || file.clientId || "",
    channel: String(process.env.TWITCH_CHANNEL || file.channel || "").toLowerCase().replace(/^#/, ""),
  };
}

module.exports = { loadConfig, CONFIG_PATH };
