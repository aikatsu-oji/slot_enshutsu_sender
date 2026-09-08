// Twitch チャットを匿名で読む (IRC over WebSocket)。
//
// EventSub と違って **認証がいらない**。アプリ登録も OAuth も無しで、チャンネル名だけで
// 誰でも公開チャットを読める (justinfan で匿名ログインする Twitch 公認の入り口)。
// コメントで動かすぶんにはこれで足り、配信前の準備がチャンネル名の指定だけになる。
//
// ここでは受信と再接続だけを持ち、届いた行を EventSub の channel.chat.message と
// 同じ形に整えて渡す。ルール判定は twitch_bridge.js 側で共通のまま。
//
// できないこと: チャンネルポイント・ビッツ・サブスク・レイド・フォローは IRC では取れない
//              (EventSub が要る)。それらを使うなら従来どおり認証して起動する。

const WebSocket = require("ws");

const DEFAULT_CHAT_URL = "wss://irc-ws.chat.twitch.tv:443";
const BACKOFF_MS = [1000, 2000, 4000, 8000, 16000, 30000];

// "@a=1;b=2 :nick!nick@nick.tmi.twitch.tv PRIVMSG #ch :本文" を分解する
function parseLine(line) {
  let rest = line;
  const tags = {};
  if (rest.startsWith("@")) {
    const sp = rest.indexOf(" ");
    for (const kv of rest.slice(1, sp).split(";")) {
      const i = kv.indexOf("=");
      if (i < 0) continue;
      // IRCv3 のエスケープ (\s = 空白, \: = セミコロン ...) を戻す
      tags[kv.slice(0, i)] = kv.slice(i + 1)
        .replace(/\\s/g, " ").replace(/\\:/g, ";").replace(/\\r/g, "").replace(/\\n/g, "")
        .replace(/\\\\/g, "\\");
    }
    rest = rest.slice(sp + 1);
  }
  let prefix = "";
  if (rest.startsWith(":")) {
    const sp = rest.indexOf(" ");
    prefix = rest.slice(1, sp);
    rest = rest.slice(sp + 1);
  }
  const sp = rest.indexOf(" ");
  const command = sp < 0 ? rest : rest.slice(0, sp);
  const params = sp < 0 ? "" : rest.slice(sp + 1);
  return { tags, prefix, command, params };
}

// "moderator/1,subscriber/12" → EventSub と同じ [{set_id, id}]
function parseBadges(s) {
  return String(s || "").split(",").filter(Boolean).map((b) => {
    const [set_id, id] = b.split("/");
    return { set_id, id: id || "1" };
  });
}

/**
 * 匿名でチャンネルのチャットを読む。
 * onMessage には EventSub の channel.chat.message と同じ形の event を渡す。
 */
function createChatIrc(opts) {
  const {
    channel,
    url = DEFAULT_CHAT_URL,
    onMessage = () => {},
    onStatus = () => {},
    log = () => {},
  } = opts;

  const chan = "#" + String(channel || "").toLowerCase().replace(/^#/, "");
  let ws = null;
  let timer = null;
  let attempt = 0;
  let closed = false;

  function schedule() {
    if (closed || timer) return;
    const wait = BACKOFF_MS[Math.min(attempt, BACKOFF_MS.length - 1)];
    attempt += 1;
    onStatus(false, `再接続待ち (${Math.round(wait / 1000)} 秒)`);
    timer = setTimeout(() => { timer = null; connect(); }, wait);
  }

  function connect() {
    if (closed) return;
    ws = new WebSocket(url);

    ws.on("open", () => {
      // 匿名ログイン。justinfan + 数字なら誰でも読み取り専用で入れる。
      // tags を要求しないと表示名もバッジも取れないので必ず付ける。
      ws.send("CAP REQ :twitch.tv/tags twitch.tv/commands");
      ws.send("NICK justinfan" + (10000 + Math.floor(Math.random() * 80000)));
      ws.send("JOIN " + chan);
    });

    ws.on("message", (raw) => {
      for (const line of raw.toString().split("\r\n")) {
        if (!line) continue;
        const m = parseLine(line);

        if (m.command === "PING") { ws.send("PONG :tmi.twitch.tv"); continue; }

        if (m.command === "001") {
          attempt = 0;
          onStatus(true, `${chan} のチャットに接続しました (匿名)`);
          continue;
        }

        if (m.command === "NOTICE" && /msg_channel_suspended|No such channel/i.test(m.params)) {
          log(`[チャット] ${chan} に入れません: ${m.params}`);
          continue;
        }

        if (m.command !== "PRIVMSG") continue;
        const i = m.params.indexOf(" :");
        if (i < 0) continue;
        const text = m.params.slice(i + 2);
        const login = (m.prefix.split("!")[0] || "").toLowerCase();

        onMessage({
          message_id: m.tags.id || `irc-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
          chatter_user_login: login,
          chatter_user_name: m.tags["display-name"] || login,
          message: { text },
          badges: parseBadges(m.tags.badges),
        });
      }
    });

    ws.on("close", () => {
      ws = null;
      if (closed) return;
      onStatus(false, "切断されました");
      schedule();
    });

    ws.on("error", (e) => {
      log(`[チャット] 接続エラー: ${e.message}`);
      try { ws.close(); } catch (err) { /* 無視 */ }
    });
  }

  return {
    start() { connect(); },
    close() {
      closed = true;
      clearTimeout(timer);
      if (ws) { try { ws.close(); } catch (e) { /* 無視 */ } }
      ws = null;
    },
    get connected() { return !!ws && ws.readyState === WebSocket.OPEN; },
  };
}

module.exports = { createChatIrc, parseLine, parseBadges, DEFAULT_CHAT_URL };
