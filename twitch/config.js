// 設定の読み取りだけ。
//
// いま要るのは **チャンネル名ひとつ** です。チャットは匿名で読めるので、
// 認証もアプリ登録もクライアント ID も要りません。
//
// 置き場所 (.gitignore 済みの .run/ 配下。無ければ無いで動く):
//   .run/twitch_config.json  { "channel": "..." }
// 環境変数 TWITCH_CHANNEL があればそれが最優先。
// コマンドラインの --chat <channel> はさらに優先されます (ブリッジ側で解決)。

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

// 足りなくてもここでは投げない。無いときに何を言うかは使う側 (ブリッジ) が決める。
function loadConfig() {
  const file = readJson(CONFIG_PATH) || {};
  return {
    channel: String(process.env.TWITCH_CHANNEL || file.channel || "").toLowerCase().replace(/^#/, ""),
  };
}

module.exports = { loadConfig, CONFIG_PATH };
