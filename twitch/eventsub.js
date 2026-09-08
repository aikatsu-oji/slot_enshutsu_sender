// EventSub WebSocket の接続・再接続・keepalive 監視・重複排除だけを持つ薄い層。
//
// ここには Twitch の「通知の中身」に関する知識を一切置かない。
// 何を購読するか / 届いた通知をどう解釈するかは twitch_bridge.js の仕事。
//
// 面倒を見るのは次の 4 つ:
//   1. keepalive が途絶えたら死んだとみなして張り直す (既定 10 秒間隔 × 3 で判定)
//   2. session_reconnect は新しい URL に「先に繋いでから」旧を閉じる (取りこぼし防止)
//   3. 同じ message_id の再送を捨てる (直近 1000 件)
//   4. 10 分以上前のメッセージを捨てる (再送の取り違え防止)

const WebSocket = require("ws");

const DEFAULT_URL = "wss://eventsub.wss.twitch.tv/ws";
const DEDUP_MAX = 1000;
const STALE_MS = 10 * 60 * 1000;
const BACKOFF_MS = [1000, 2000, 4000, 8000, 16000, 30000];

function createEventSub(opts) {
  const {
    url = DEFAULT_URL,
    onWelcome = () => {},        // (sessionId, { reconnected }) 購読の登録はここで行う
    onNotification = () => {},   // (subscriptionType, event, meta)
    onRevocation = () => {},     // (subscription)
    onStatus = () => {},         // (connected, text)
    log = () => {},
  } = opts;

  let ws = null;                 // 現在の接続
  let pending = null;            // session_reconnect で張り替え中の新しい接続
  let watchdog = null;
  let retryTimer = null;
  let attempt = 0;
  let closed = false;
  let keepaliveSec = 10;

  const seen = new Set();
  const seenOrder = [];

  function isDuplicate(id) {
    if (!id) return false;
    if (seen.has(id)) return true;
    seen.add(id);
    seenOrder.push(id);
    if (seenOrder.length > DEDUP_MAX) seen.delete(seenOrder.shift());
    return false;
  }

  function armWatchdog() {
    clearTimeout(watchdog);
    // keepalive 3 回ぶん + 余裕。これを過ぎたら接続が死んだとみなす。
    watchdog = setTimeout(() => {
      log(`[Twitch] keepalive が ${keepaliveSec * 3 + 5} 秒途絶えました。接続し直します`);
      hardReconnect();
    }, (keepaliveSec * 3 + 5) * 1000);
  }

  function hardReconnect() {
    if (closed) return;
    if (ws) {
      try { ws.terminate(); } catch (e) { /* すでに死んでいる */ }
      ws = null;
    }
    scheduleConnect(url);
  }

  function scheduleConnect(target) {
    if (closed || retryTimer) return;
    const wait = BACKOFF_MS[Math.min(attempt, BACKOFF_MS.length - 1)];
    attempt += 1;
    onStatus(false, `再接続待ち (${Math.round(wait / 1000)} 秒)`);
    retryTimer = setTimeout(() => {
      retryTimer = null;
      connect(target);
    }, wait);
  }

  function connect(target = url, isReconnect = false) {
    if (closed) return;
    const socket = new WebSocket(target);
    if (isReconnect) pending = socket; else ws = socket;

    socket.on("open", () => {
      log(`[Twitch] 接続しました: ${target}`);
    });

    socket.on("message", (raw) => {
      armWatchdog();
      let msg;
      try {
        msg = JSON.parse(raw.toString());
      } catch (e) {
        return;
      }
      const meta = msg.metadata || {};
      const payload = msg.payload || {};

      if (meta.message_timestamp && Date.now() - Date.parse(meta.message_timestamp) > STALE_MS) return;
      if (isDuplicate(meta.message_id)) return;

      switch (meta.message_type) {
        case "session_welcome": {
          const session = payload.session || {};
          keepaliveSec = Number(session.keepalive_timeout_seconds) || 10;
          armWatchdog();
          attempt = 0;
          // 張り替え中だったなら、ここで旧接続を閉じて新接続を本番に昇格させる
          if (socket === pending) {
            const old = ws;
            ws = pending;
            pending = null;
            if (old) { try { old.close(); } catch (e) { /* 無視 */ } }
            log("[Twitch] 新しいセッションへ切り替えました");
          }
          onStatus(true, "接続済み");
          onWelcome(session.id, { reconnected: isReconnect });
          break;
        }
        case "session_keepalive":
          break;
        case "notification": {
          const sub = payload.subscription || {};
          onNotification(sub.type, payload.event || {}, meta);
          break;
        }
        case "session_reconnect": {
          const next = (payload.session || {}).reconnect_url;
          if (next) {
            log("[Twitch] session_reconnect を受け取りました。新しい URL へ繋ぎ直します");
            connect(next, true);
          }
          break;
        }
        case "revocation":
          onRevocation(payload.subscription || {});
          break;
        default:
          break;
      }
    });

    socket.on("close", () => {
      if (socket === pending) { pending = null; return; }   // 張り替えに失敗しただけ
      if (socket !== ws) return;                            // すでに捨てた接続
      clearTimeout(watchdog);
      ws = null;
      if (closed) return;
      onStatus(false, "切断されました");
      scheduleConnect(url);
    });

    socket.on("error", (e) => {
      log(`[Twitch] 接続エラー: ${e.message}`);
      try { socket.close(); } catch (err) { /* 無視 */ }
    });
  }

  return {
    start() { connect(url); },
    close() {
      closed = true;
      clearTimeout(watchdog);
      clearTimeout(retryTimer);
      for (const s of [ws, pending]) if (s) { try { s.close(); } catch (e) { /* 無視 */ } }
      ws = pending = null;
    },
    get connected() { return !!ws && ws.readyState === WebSocket.OPEN; },
  };
}

module.exports = { createEventSub, DEFAULT_URL };
