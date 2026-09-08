// Twitch → 中継サーバー ブリッジ
//
// Twitch の EventSub から届くイベントを正規化し、rules.json に従って
// 中継サーバー (ws://127.0.0.1:8787) へ流す。中継サーバーは一切改造しない。
//
//   node twitch/twitch_bridge.js                     本番 (Twitch に繋ぐ)
//   node twitch/twitch_bridge.js --mock twitch/mock_events.jsonl --speed 5
//                                                    Twitch に繋がずに擬似イベントを流す
//   node twitch/twitch_bridge.js --ws-url ws://127.0.0.1:8080/ws --no-subscribe
//                                                    Twitch CLI のモック EventSub サーバーに繋ぐ
//
// やること:
//   演出 (予告バナー) をオーバーレイへ直接流し、無料アクションぶんのメダルを主制御へ投入する。
//   押し順の投票と、モデレータ限定の操作 (設定変更) も中継する。
//   **抽選には触らない。** 触る口は --allow-force を両方に付けたときだけ開く (既定は閉じている)。
//   --no-medals を付けると演出だけになる。
//
// 演出の送り先に subEvent を使う理由:
//   {"action":"subEvent","event":{...}} はオーバーレイが 8787 で直接受ける。
//   panelInject(layer:"enshutsu") は主制御を経由するので、主制御が起動していないと届かない。
//   演出だけなら「中継サーバー + オーバーレイ」だけで動く (主制御を起動しなくてよい)。

const fs = require("fs");
const path = require("path");
const WebSocket = require("ws");

const { createEventSub, DEFAULT_URL } = require("./eventsub");

const ROOT = path.resolve(__dirname, "..");
const RANKS = ["白", "青", "緑", "赤", "金"];

// ---------------------------------------------------------------------------
// 引数
// ---------------------------------------------------------------------------
function parseArgs(argv) {
  const a = {
    relay: "ws://127.0.0.1:8787",
    rules: path.join(__dirname, "rules.json"),
    wsUrl: DEFAULT_URL,
    apiUrl: null,
    mock: null,
    speed: 1,
    medals: true,
    subscribe: true,
    dryRun: false,
    allowForce: false,
  };
  for (let i = 2; i < argv.length; i++) {
    const v = argv[i];
    const next = () => argv[++i];
    if (v === "--relay") a.relay = next();
    else if (v === "--rules") a.rules = next();
    else if (v === "--ws-url") a.wsUrl = next();
    else if (v === "--api-url") a.apiUrl = next();
    else if (v === "--mock") a.mock = next();
    else if (v === "--speed") a.speed = Math.max(0.1, Number(next()) || 1);
    else if (v === "--medals") a.medals = true;
    else if (v === "--no-medals") a.medals = false;
    else if (v === "--allow-force") a.allowForce = true;
    else if (v === "--no-subscribe") a.subscribe = false;
    else if (v === "--dry-run") a.dryRun = true;
    else if (v === "--help" || v === "-h") { printHelp(); process.exit(0); }
    else { console.error(`不明な引数: ${v}`); printHelp(); process.exit(2); }
  }
  return a;
}

function printHelp() {
  console.log(`使い方: node twitch/twitch_bridge.js [options]

  --relay <url>     中継サーバー          (既定 ws://127.0.0.1:8787)
  --rules <file>    ルール表              (既定 twitch/rules.json)
  --ws-url <url>    EventSub の接続先     (既定 ${DEFAULT_URL})
  --api-url <url>   Helix の接続先        (Twitch CLI のモックを使うときだけ)
  --mock <file>     擬似イベント JSONL を流す (Twitch に繋がない)
  --speed <n>       --mock の再生倍率     (既定 1 = 1.5 秒に 1 件)
  --no-medals       メダルの投入をしない (段階1 の演出だけの挙動に戻す)
  --allow-force     強制フラグ (イベント用のやらせ) を送れるようにする。既定は無効。
                    主制御側にも --allow-force が要る (両方に付けないと効かない)
  --no-subscribe    購読登録をしない      (モックサーバー向け)
  --dry-run         中継へ送らずログだけ出す`);
}

const args = parseArgs(process.argv);

// ---------------------------------------------------------------------------
// ログ
// ---------------------------------------------------------------------------
function stamp() {
  const d = new Date();
  const p = (n) => String(n).padStart(2, "0");
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}
const log = (...m) => console.log(stamp(), ...m);

// ---------------------------------------------------------------------------
// サニタイズ
//   表示名はオーバーレイ (配信画面) に出るので、制御文字と表示方向の上書き文字を必ず落とす。
//   U+202E などを残すと、視聴者が名前で画面のレイアウトを壊せてしまう。
// ---------------------------------------------------------------------------
const UNSAFE = new RegExp(
  "[\\u0000-\\u001F\\u007F\\u200B-\\u200F\\u2028-\\u202E\\u2066-\\u2069]", "g");

function safeName(s) {
  const t = String(s == null ? "" : s).replace(UNSAFE, "").trim().slice(0, 20);
  return t || "名無し";
}

function safeText(s, max = 120) {
  return String(s == null ? "" : s).replace(UNSAFE, "").trim().slice(0, max);
}

// ---------------------------------------------------------------------------
// ルール表
// ---------------------------------------------------------------------------
const DEFAULT_GUARD = {
  userCooldownSec: 5,
  maxMedalsPerEvent: 2000,
  maxMedalsPerMinute: 6000,
  chatMedalsPerMinute: 10,
  enshutsuMinIntervalMs: 1500,
  modOnlyInputs: [],
  mods: [],            // モデレータの login 名。チャット以外 (チャンネルポイントなど) は
                       // バッジが付いてこないので、ここに書いた名前で判定する
  ignoreUsers: [],
  chatHype: { windowSec: 60, threshold: 30, boost: 1 },
};

function loadRules(file) {
  const raw = JSON.parse(fs.readFileSync(file, "utf-8"));
  if (!Array.isArray(raw.rules)) throw new Error("rules が配列ではありません");
  raw.rules.forEach((r, i) => {
    if (!r || typeof r !== "object") throw new Error(`rules[${i}] がオブジェクトではありません`);
    if (!r.when || typeof r.when.kind !== "string") {
      throw new Error(`rules[${i}] (${r.name || "?"}): when.kind がありません`);
    }
    if (r.when.match) new RegExp(r.when.match);        // 壊れた正規表現はここで弾く
    if (r.counter && Array.isArray(r.counter.ignore)) r.counter.ignore.forEach((p) => new RegExp(p));
  });
  const guard = { ...DEFAULT_GUARD, ...(raw.guard || {}) };
  guard.chatHype = { ...DEFAULT_GUARD.chatHype, ...(raw.guard && raw.guard.chatHype) };
  guard.ignoreUsers = (guard.ignoreUsers || []).map((s) => String(s).toLowerCase());
  guard.mods = (guard.mods || []).map((s) => String(s).toLowerCase());
  guard.modOnlyInputs = guard.modOnlyInputs || [];
  return { enabled: raw.enabled !== false, guard, rules: raw.rules };
}

let rules = { enabled: false, guard: DEFAULT_GUARD, rules: [] };

function reloadRules(why) {
  try {
    rules = loadRules(args.rules);
    log(`[ルール] 読み込みました (${rules.rules.length} 件${rules.enabled ? "" : " / enabled=false"})` +
        `${why ? " - " + why : ""}`);
    return true;
  } catch (e) {
    // 壊れていても落とさない。連携 OFF のまま起動し、直して再読込すれば復帰できる。
    log(`[ルール] 読み込みに失敗しました。連携 OFF で続行します: ${e.message}`);
    rules = { enabled: false, guard: DEFAULT_GUARD, rules: [] };
    return false;
  }
}

// ---------------------------------------------------------------------------
// 中継サーバーとの接続
//   落ちている間の送信は捨てずに貯める (視聴者が押したぶんを失わないため)。
// ---------------------------------------------------------------------------
function createRelay(url, onMessage) {
  let ws = null;
  let timer = null;
  let closed = false;
  const pending = [];
  const PENDING_MAX = 500;

  function flush() {
    while (pending.length && ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(pending.shift()));
    }
  }

  function connect() {
    if (closed) return;
    ws = new WebSocket(url);
    ws.on("open", () => {
      log(`[中継] 接続しました: ${url}${pending.length ? ` (保留 ${pending.length} 件を送ります)` : ""}`);
      flush();
    });
    ws.on("message", (raw) => {
      let d;
      try { d = JSON.parse(raw.toString()); } catch (e) { return; }
      if (d && typeof d.action === "string") onMessage(d);
    });
    ws.on("close", () => {
      ws = null;
      if (closed) return;
      if (!timer) timer = setTimeout(() => { timer = null; connect(); }, 3000);
    });
    ws.on("error", (e) => {
      log(`[中継] 接続エラー: ${e.message}`);
      try { ws.close(); } catch (err) { /* 無視 */ }
    });
  }

  connect();
  return {
    send(obj) {
      if (args.dryRun) { log("[dry-run]", JSON.stringify(obj)); return; }
      if (ws && ws.readyState === WebSocket.OPEN) { ws.send(JSON.stringify(obj)); return; }
      pending.push(obj);
      while (pending.length > PENDING_MAX) pending.shift();
    },
    get connected() { return !!ws && ws.readyState === WebSocket.OPEN; },
    get pending() { return pending.length; },
    close() {
      closed = true;
      clearTimeout(timer);
      if (ws) { try { ws.close(); } catch (e) { /* 無視 */ } }
    },
  };
}

// ---------------------------------------------------------------------------
// 購読するイベント (kind → EventSub の購読定義)
//   ルール表に出てこない kind は購読しない (1 セッションの購読数に上限があるため)。
//   ※ type / version / スコープ名は Twitch 側の改訂があるので、動かないときは
//      https://dev.twitch.tv/docs/eventsub/eventsub-subscription-types/ で確認する。
// ---------------------------------------------------------------------------
const bc = (id) => ({ broadcaster_user_id: id });
const SUBSCRIPTIONS = {
  follow:  [{ type: "channel.follow", version: "2", scope: "moderator:read:followers",
              cond: (id) => ({ broadcaster_user_id: id, moderator_user_id: id }) }],
  cheer:   [{ type: "channel.cheer", version: "1", scope: "bits:read", cond: bc }],
  sub:     [{ type: "channel.subscribe", version: "1", scope: "channel:read:subscriptions", cond: bc }],
  resub:   [{ type: "channel.subscription.message", version: "1",
              scope: "channel:read:subscriptions", cond: bc }],
  subgift: [{ type: "channel.subscription.gift", version: "1",
              scope: "channel:read:subscriptions", cond: bc }],
  raid:    [{ type: "channel.raid", version: "1", scope: null,
              cond: (id) => ({ to_broadcaster_user_id: id }) }],
  redeem:  [{ type: "channel.channel_points_custom_reward_redemption.add", version: "1",
              scope: "channel:read:redemptions", cond: bc }],
  chat:    [{ type: "channel.chat.message", version: "1", scope: "user:read:chat",
              cond: (id) => ({ broadcaster_user_id: id, user_id: id }) }],
  stream:  [{ type: "stream.online", version: "1", scope: null, cond: bc },
            { type: "stream.offline", version: "1", scope: null, cond: bc }],
};

// stream は購読しないと連帯カウンタと初コメ判定のリセット契機が無いので常に取る
function neededKinds() {
  const kinds = new Set(["stream"]);
  for (const r of rules.rules) kinds.add(r.when.kind);
  return [...kinds].filter((k) => SUBSCRIPTIONS[k]);
}

// ---------------------------------------------------------------------------
// 正規化 - ここから先は Twitch 固有の形を持ち込まない
// ---------------------------------------------------------------------------
const TIER = { 1000: 1, 2000: 2, 3000: 3 };

function badgeFlags(badges) {
  const set = new Set((badges || []).map((b) => b && b.set_id));
  return { isMod: set.has("moderator") || set.has("broadcaster"), isSub: set.has("subscriber") };
}

function normalize(type, ev, meta) {
  const id = (meta && meta.message_id) || ev.id || `${type}:${Date.now()}`;
  const at = (meta && meta.message_timestamp) || new Date().toISOString();
  const base = { id, at, kind: null, user: null, amount: 0, tier: 0, reward: "", text: "" };
  const user = (login, name, extra) => ({
    login: String(login || "").toLowerCase(),
    name: safeName(name || login),
    isMod: false, isSub: false, ...(extra || {}),
  });

  switch (type) {
    case "channel.follow":
      return { ...base, kind: "follow", user: user(ev.user_login, ev.user_name) };

    case "channel.cheer":
      return { ...base, kind: "cheer", amount: Number(ev.bits) || 0,
               text: safeText(ev.message),
               user: ev.is_anonymous ? user("", "匿名さん") : user(ev.user_login, ev.user_name) };

    case "channel.subscribe":
      // ギフトされた側にも飛んでくる。ギフトはギフトした側 (channel.subscription.gift) で数えるので、
      // ここでは kind を分けて、ルールに一致させない。
      return { ...base, kind: ev.is_gift ? "subgift_recipient" : "sub",
               tier: TIER[ev.tier] || 1, user: user(ev.user_login, ev.user_name) };

    case "channel.subscription.message":
      return { ...base, kind: "resub", tier: TIER[ev.tier] || 1,
               amount: Number(ev.cumulative_months) || 0,
               text: safeText(ev.message && ev.message.text),
               user: user(ev.user_login, ev.user_name) };

    case "channel.subscription.gift":
      return { ...base, kind: "subgift", tier: TIER[ev.tier] || 1, amount: Number(ev.total) || 1,
               user: ev.is_anonymous ? user("", "匿名さん") : user(ev.user_login, ev.user_name) };

    case "channel.raid":
      return { ...base, kind: "raid", amount: Number(ev.viewers) || 0,
               user: user(ev.from_broadcaster_user_login, ev.from_broadcaster_user_name) };

    case "channel.channel_points_custom_reward_redemption.add":
      return { ...base, kind: "redeem", id: ev.id || id,
               reward: safeText(ev.reward && ev.reward.title, 60),
               amount: Number(ev.reward && ev.reward.cost) || 0,
               text: safeText(ev.user_input),
               user: user(ev.user_login, ev.user_name) };

    case "channel.chat.message":
      return { ...base, kind: "chat", id: ev.message_id || id,
               text: safeText(ev.message && ev.message.text),
               user: user(ev.chatter_user_login, ev.chatter_user_name, badgeFlags(ev.badges)) };

    case "stream.online":
      return { ...base, kind: "stream", text: "online" };
    case "stream.offline":
      return { ...base, kind: "stream", text: "offline" };

    default:
      return null;
  }
}

// ---------------------------------------------------------------------------
// 演出の流量制御
//   最短間隔で間引き、待っている間に来た同じ演出は 1 件に合流する
//   (レイドや連続ギフトでバナーが数十枚飛ぶのを防ぐ)。
// ---------------------------------------------------------------------------
function createEnshutsuQueue(send) {
  const queue = [];
  let last = 0;
  let timer = null;

  // 合流は「同じ種類・同じランク・同じきっかけ」だけ。trigger を入れないと、
  // たまたま同じランクになった別のイベント (フォローとビッツ など) が 1 件に潰れてしまう。
  const key = (ev) => `${ev.type}:${ev.rank || ""}:${ev.trigger || ""}`;

  function pump() {
    if (timer || !queue.length) return;
    const wait = Math.max(0, rules.guard.enshutsuMinIntervalMs - (Date.now() - last));
    timer = setTimeout(() => {
      timer = null;
      const item = queue.shift();
      if (item) {
        last = Date.now();
        const ev = { ...item.ev };
        if (item.by.length > 1) {
          ev.by = item.by.slice(0, 3).join(" ") + (item.by.length > 3 ? ` ほか${item.by.length - 3}人` : "");
          ev.merged = item.by.length;
        }
        send({ action: "subEvent", event: ev });
        log(`[演出] ${ev.type} ${ev.rank || ""} <- ${ev.by || "-"}` +
            `${ev.merged ? ` (${ev.merged}件を合流)` : ""}`);
      }
      pump();
    }, wait);
  }

  return {
    push(ev, by) {
      const k = key(ev);
      const found = queue.find((q) => key(q.ev) === k);
      if (found) { if (by) found.by.push(by); return; }
      queue.push({ ev, by: by ? [by] : [] });
      pump();
    },
    get size() { return queue.length; },
  };
}

// ---------------------------------------------------------------------------
// ルール判定
// ---------------------------------------------------------------------------
const chatHits = [];          // チャット密度 (ハイプ) 用の時刻リスト
const seenChatters = new Set();
const cooldown = new Map();   // login -> 次に受け付けてよい時刻

function bumpRank(rank, steps) {
  const i = RANKS.indexOf(rank);
  if (i < 0 || !steps) return rank;
  return RANKS[Math.min(RANKS.length - 1, i + steps)];
}

function chatDensity() {
  const w = rules.guard.chatHype.windowSec * 1000;
  const now = Date.now();
  while (chatHits.length && now - chatHits[0] > w) chatHits.shift();
  return chatHits.length;
}

function matches(when, ev) {
  if (when.kind !== ev.kind) return false;
  if (when.reward != null && when.reward !== ev.reward) return false;
  if (when.first === true && !ev._first) return false;
  if (when.match && !new RegExp(when.match).test(ev.text || "")) return false;
  if (when.minTier != null && (ev.tier || 0) < when.minTier) return false;
  return true;
}

function resolveEnshutsu(spec, ev) {
  const out = { type: spec.type || "banner" };
  let rank = spec.rank || "白";
  if (Array.isArray(spec.rankBy)) {
    for (const [threshold, r] of spec.rankBy) if ((ev.amount || 0) >= threshold) rank = r;
  } else if (spec.rankByTier) {
    rank = spec.rankByTier[String(ev.tier || 1)] || rank;
  }
  // チャットが伸びているときは 1 段だけ底上げする (出玉には無関係の Tier 1 演出)
  const hype = rules.guard.chatHype;
  if (hype.boost && chatDensity() >= hype.threshold) rank = bumpRank(rank, hype.boost);
  out.rank = rank;
  out.trigger = ev.kind;
  if (ev.user && ev.user.name) out.by = ev.user.name;
  return out;
}

// ---------------------------------------------------------------------------
// メダルの送出
//   主制御へ送るのは {action:"playerInput", input:"insertMedal"} だけ。抽選には触らない。
//
//   1分あたりの上限はトークンバケットで持つ。上限に当たったぶんは捨てずに待たせる。
//   「コメントしたのに何も起きなかった」「ビッツが消えた」を作らないため、
//   ここでイベントを落とすことは絶対にしない。
//   チャット由来 (連帯カウンタ) だけ別バケツにするのは、原資が「タイピング」で
//   いくらでも増やせるから。チャンネルポイントは視聴時間ぶんしか貯まらないので緩い。
// ---------------------------------------------------------------------------
function createMedalQueue(send) {
  const q = [];                      // [{ n, src, chat }]
  let chatTokens = 0, genTokens = 0;
  let last = Date.now();
  let sentTotal = 0;

  function tick() {
    const now = Date.now();
    const dt = Math.max(0, now - last) / 60000;   // 分
    last = now;
    const chatMax = Math.max(0, rules.guard.chatMedalsPerMinute);
    const genMax = Math.max(0, rules.guard.maxMedalsPerMinute);
    // 溜めすぎないよう 60 分ぶんで頭打ちにする (長い無風のあとに一気に出ないように)
    chatTokens = Math.min(chatMax * 60, chatTokens + chatMax * dt);
    genTokens = Math.min(genMax * 60, genTokens + genMax * dt);

    // 先頭から順に、まるごと出せるものだけ出す (分割すると「100回転」が細切れになる)
    for (let i = 0; i < q.length; i++) {
      const it = q[i];
      const tokens = it.chat ? chatTokens : genTokens;
      if (tokens < it.n) continue;
      if (it.chat) chatTokens -= it.n; else genTokens -= it.n;
      q.splice(i, 1);
      i--;
      sentTotal += it.n;
      send({ action: "playerInput", input: "insertMedal", medals: it.n, src: it.src, reqId: it.id });
      log(`[投入] ${it.src} ${it.n}枚${it.note ? ` (${it.note})` : ""}`);
    }
  }

  setInterval(tick, 1000);
  return {
    push(n, src, opts) {
      n = Math.floor(Number(n) || 0);
      if (n <= 0) return;
      const cap = Math.max(1, rules.guard.maxMedalsPerEvent);
      if (n > cap) {
        log(`[投入] ${src} の ${n}枚 は 1イベント上限 ${cap}枚 に丸めました`);
        n = cap;
      }
      q.push({ n, src, chat: !!(opts && opts.chat), note: opts && opts.note,
               id: (opts && opts.id) || `m${Date.now()}` });
      tick();
    },
    get waiting() { return q.reduce((a, b) => a + b.n, 0); },
    get sent() { return sentTotal; },
  };
}

// medals 指定を枚数に変換する。
//   { fixed: 120 }                          … 固定
//   { of:"amount", unit:20, max:1000 }      … amount 1 につき unit 枚
//   { of:"amount", per:10 }                 … amount per につき 1 枚
//   { byTier:{ "1":500 } }                  … ティア別 (無料設計では使わない)
function resolveMedals(spec, ev) {
  if (!spec || typeof spec !== "object") return 0;
  let n = 0;
  if (spec.fixed != null) {
    n = Number(spec.fixed) || 0;
  } else if (spec.byTier) {
    n = Number(spec.byTier[String(ev.tier || 1)]) || 0;
  } else if (spec.of) {
    const base = Number(ev[spec.of]) || 0;
    if (spec.unit != null) n = base * (Number(spec.unit) || 0);
    else if (spec.per != null) n = base / (Number(spec.per) || 1);
    else n = base;
  }
  n = Math.floor(n);
  if (spec.max != null) n = Math.min(n, Number(spec.max) || 0);
  return Math.max(0, n);
}

// ---------------------------------------------------------------------------
// チャット連帯カウンタ
//   個人にではなくチャンネル全体に貯める。1人が200回書いても200人が1回ずつでも同じなので、
//   連投しても得しない = スパム対策が構造で効く。
// ---------------------------------------------------------------------------
const chatCounter = { count: 0, goal: 0, lastByUser: new Map(), lastText: "" };

function countChat(rule, ev, ctx) {
  const c = rule.counter;
  chatCounter.goal = Number(c.goal) || 200;
  const text = ev.text || "";
  if (text.length < (Number(c.minLength) || 0)) return;
  if (Array.isArray(c.ignore) && c.ignore.some((p) => new RegExp(p).test(text))) return;
  if (text === chatCounter.lastText) return;                 // 直前と同じ本文は数えない
  const cd = (Number(c.perUserCooldownSec) || 0) * 1000;
  const login = ev.user && ev.user.login;
  if (login && cd) {
    const until = chatCounter.lastByUser.get(login) || 0;
    if (Date.now() < until) return;                          // 同じ人の連投は数えない
    chatCounter.lastByUser.set(login, Date.now() + cd);
  }
  chatCounter.lastText = text;
  chatCounter.count++;
  if (chatCounter.count < chatCounter.goal) return;
  chatCounter.count = 0;
  const medals = Number(c.medals) || 0;
  ctx.medals.push(medals, "チャット", { chat: true, note: `${chatCounter.goal}コメント達成` });
  log(`[チャット] ${chatCounter.goal}コメント達成 → ${medals}枚 (${c.label || ""})`);
}

// ---------------------------------------------------------------------------
// 押し順投票 / 遊技者の操作
// ---------------------------------------------------------------------------
// チャット以外 (チャンネルポイントなど) にはバッジが付いてこないので、
// guard.mods に書いた login 名でモデレータ判定する。
function isMod(ev) {
  if (ev.user && ev.user.isMod) return true;
  const login = ev.user && ev.user.login;
  return !!(login && rules.guard.mods.includes(login));
}

// 第一停止だけ投票で決める。残りの2択は運 (実機で「左から押す」と言うのと同じ)。
const FIRST_REEL_ORDER = { "左": 0, "中": 2, "右": 4 };

const vote = { open: false, tally: new Map(), voters: new Set(), timer: null };

function castVote(rule, ev, ctx) {
  const m = new RegExp(rule.when.match).exec(ev.text || "");
  const key = m && m[1];
  if (!key || FIRST_REEL_ORDER[key] == null) return;
  const sec = Number(rule.vote.windowSec) || 3;
  if (!vote.open) {
    vote.open = true;
    vote.tally.clear();
    vote.voters.clear();
    clearTimeout(vote.timer);
    vote.timer = setTimeout(() => closeVote(ctx), sec * 1000);
    log(`[押し順] 投票を開始しました (${sec}秒)`);
  }
  const login = ev.user && ev.user.login;
  if (login && vote.voters.has(login)) return;     // 1人1票
  if (login) vote.voters.add(login);
  vote.tally.set(key, (vote.tally.get(key) || 0) + 1);
}

function closeVote(ctx) {
  vote.open = false;
  const top = [...vote.tally.entries()].sort((a, b) => b[1] - a[1])[0];
  if (!top) return;
  const order = FIRST_REEL_ORDER[top[0]] + Math.floor(Math.random() * 2);
  ctx.relay.send({ action: "playerInput", input: "stopOrder", order });
  const detail = [...vote.tally.entries()].map(([k, n]) => `${k}${n}`).join(" ");
  log(`[押し順] ${top[0]}第一停止 に決まりました (${detail}) → 押し順${order + 1}`);
}

// powerCycle / pause / resume。抽選には触らない。
function applyInput(rule, ev, ctx) {
  const inp = rule.input;
  const needsMod = rule.requires === "mod" || rules.guard.modOnlyInputs.includes(inp);
  if (needsMod && !isMod(ev)) {
    log(`[操作] ${rule.name || inp}: モデレータ限定なので無視しました (${ev.user && ev.user.name})`);
    return;
  }
  if (inp === "powerCycle") {
    // クレジット制は設定3以上だと下皿が増え続けて止まらなくなるので、既定は 1〜2 から選ぶ
    const choices = Array.isArray(rule.settingChoices) && rule.settingChoices.length
      ? rule.settingChoices : [1, 2];
    const setting = choices[Math.floor(Math.random() * choices.length)];
    ctx.relay.send({ action: "playerInput", input: "powerCycle", setting });
    log("[操作] 設定変更 (打ち直し) を送りました");
  } else if (inp === "pause" || inp === "resume") {
    ctx.relay.send({ action: "playerInput", input: inp });
    log(`[操作] ${inp} を送りました`);
  } else {
    log(`[操作] 未対応の input: ${inp}`);
  }
}

// 強制フラグ (Tier 3)。ブリッジと主制御の両方に --allow-force が要る。
function applyForce(rule, ev, ctx) {
  if (!args.allowForce) {
    log(`[強制] ${rule.name || "force"} は無効です (--allow-force が要ります)`);
    return;
  }
  if (!isMod(ev)) {
    log(`[強制] ${rule.name || "force"}: モデレータ限定なので無視しました`);
    return;
  }
  ctx.relay.send({ action: "playerInput", input: "forceFlag", flag: rule.force.flag });
  log(`[強制] 次ゲームを ${rule.force.flag} に指定しました (演出)`);
}

function handleEvent(ev, ctx) {
  if (!rules.enabled) return;
  if (ev.user && ev.user.login && rules.guard.ignoreUsers.includes(ev.user.login)) return;

  // 配信開始で「その配信の初コメント」判定をリセットする
  if (ev.kind === "stream") {
    if (ev.text === "online") {
      seenChatters.clear();
      chatHits.length = 0;
      chatCounter.count = 0;
      chatCounter.lastByUser.clear();
      chatCounter.lastText = "";
      log("[Twitch] 配信開始を検知。初コメ判定と連帯カウンタをリセットしました");
    }
    ctx.relay.send({ action: "twitchEvent", ev });
    return;
  }

  if (ev.kind === "chat") {
    chatHits.push(Date.now());
    ev._first = !!(ev.user.login && !seenChatters.has(ev.user.login));
    if (ev.user.login) seenChatters.add(ev.user.login);
  }

  // enabled:false のルールは無効。出玉に影響する遊びは既定で切っておき、
  // 使いたくなったら rules.json で true にする。
  const hit = rules.rules.filter((r) => r.enabled !== false && matches(r.when, ev));
  if (!hit.length) return;

  // 演出は最初に一致したルールのぶんだけ (バナーが二重に出るのを防ぐ)。
  // ユーザーごとのクールダウンは「演出を連続で出さない」ためのもので、
  // イベント自体は絶対に捨てない。捨てるとチャンネルポイントやギフトが
  // 消えてしまう (視聴者はもう押している)。
  // クールダウンは「人 × 種類」で持つ。人だけで持つと、直前にフォローした視聴者の
  // ビッツにバナーが出ない、といった取りこぼしが起きる。
  const withEnshutsu = hit.find((r) => r.enshutsu);
  let fired = false;
  if (withEnshutsu) {
    const login = ev.user && ev.user.login && `${ev.user.login}:${ev.kind}`;
    const until = (login && cooldown.get(login)) || 0;
    if (Date.now() >= until) {
      if (login) cooldown.set(login, Date.now() + rules.guard.userCooldownSec * 1000);
      ctx.queue.push(resolveEnshutsu(withEnshutsu.enshutsu, ev), ev.user && ev.user.name);
      fired = true;
    }
  }

  // メダル。--no-medals なら何もしない (段階1 の挙動)。
  if (args.medals) {
    for (const r of hit) {
      if (r.medals) {
        const n = resolveMedals(r.medals, ev);
        if (n > 0) {
          ctx.medals.push(n, ev.user && ev.user.name ? ev.user.name : "視聴者",
                          { note: r.name, id: ev.id });
        }
      }
      if (r.counter && ev.kind === "chat") countChat(r, ev, ctx);
      if (r.vote && ev.kind === "chat") castVote(r, ev, ctx);
      if (r.input) applyInput(r, ev, ctx);
      if (r.force) applyForce(r, ev, ctx);
    }
  }

  // チャットは本文を中継へ流さない (流量・プライバシー・表示崩しの 3 つを同時に避ける)。
  // 何も起きなかった普通のコメントは、そもそも中継へ送らない。
  if (ev.kind === "chat") {
    if (!fired) return;
    const { _first, text, ...quiet } = ev;
    ctx.relay.send({ action: "twitchEvent", ev: quiet });
    return;
  }

  const { _first, ...clean } = ev;
  ctx.relay.send({ action: "twitchEvent", ev: clean });
}

// ---------------------------------------------------------------------------
// 起動
// ---------------------------------------------------------------------------
async function main() {
  reloadRules();

  const ctx = {};
  const relay = createRelay(args.relay, (msg) => {
    if (msg.action === "twitchControl") {
      if (typeof msg.enabled === "boolean") {
        rules.enabled = msg.enabled;
        log(`[コンパネ] 連携を ${msg.enabled ? "ON" : "OFF"} にしました`);
      }
      if (msg.reload) reloadRules("コンパネから再読込");
    } else if (msg.action === "twitchMock" && msg.ev) {
      // コンパネの疑似イベントボタン。正規化済みの形をそのまま流す。
      handleEvent({
        id: `mock:${Date.now()}`, at: new Date().toISOString(),
        amount: 0, tier: 1, reward: "", text: "",
        ...msg.ev,
        user: { login: "", name: "テスト", isMod: false, isSub: false, ...(msg.ev.user || {}) },
      }, ctx);
    }
  });
  const queue = createEnshutsuQueue((m) => relay.send(m));
  const medals = createMedalQueue((m) => relay.send(m));
  ctx.relay = relay;
  ctx.queue = queue;
  ctx.medals = medals;
  log(args.medals ? "[起動] メダル投入: 有効" : "[起動] メダル投入: 無効 (--no-medals)");
  if (args.allowForce) log("[警告] 強制フラグを許可しています (主制御側にも --allow-force が要ります)");

  // 生存と状態を 1 秒周期で流す。コンパネのランプとオーバーレイ HUD がこれを見る。
  let esConnected = false;
  let subCount = 0;
  let lastAt = null;
  const heartbeat = setInterval(() => {
    relay.send({
      action: "twitchState",
      connected: esConnected, enabled: rules.enabled, subs: subCount,
      queue: queue.size, pending: relay.pending, lastAt,
      stage: args.medals ? "medals" : "tier1",
      chatCount: chatCounter.count, chatGoal: chatCounter.goal,
      medalsWaiting: medals.waiting, medalsSent: medals.sent,
    });
  }, 1000);

  const onEvent = (type, ev, meta) => {
    const n = normalize(type, ev, meta);
    if (!n) { log(`[Twitch] 未対応の通知: ${type}`); return; }
    lastAt = n.at;
    handleEvent(n, ctx);
  };

  const shutdown = () => {
    clearInterval(heartbeat);
    if (ctx.es) ctx.es.close();
    relay.close();
    log("[終了] ブリッジを停止しました");
    process.exit(0);
  };
  process.on("SIGINT", shutdown);
  process.on("SIGTERM", shutdown);

  // --- 擬似イベントモード (Twitch に繋がない) ---
  if (args.mock) {
    esConnected = true;
    const file = path.isAbsolute(args.mock) ? args.mock : path.join(ROOT, args.mock);
    const lines = fs.readFileSync(file, "utf-8")
      .split(/\r?\n/)
      .filter((l) => l.trim() && !l.trim().startsWith("#"));
    const interval = 1500 / args.speed;
    log(`[擬似] ${lines.length} 件を ${(interval / 1000).toFixed(2)} 秒間隔で流します: ${file}`);
    let i = 0;
    const tick = setInterval(() => {
      if (i >= lines.length) {
        clearInterval(tick);
        log("[擬似] 流し終わりました (Ctrl+C で終了)");
        return;
      }
      const line = lines[i];
      i++;
      let row;
      try { row = JSON.parse(line); } catch (e) { log(`[擬似] ${i} 行目の JSON が壊れています`); return; }
      onEvent(row.type, row.event || {},
              { message_id: `mock-${i}`, message_timestamp: new Date().toISOString() });
    }, interval);
    return;
  }

  // --- 本番 / Twitch CLI のモックサーバー ---
  const { loadConfig, ensureToken, createHelix } = require("./auth");
  const config = loadConfig();
  const kinds = neededKinds();
  const wanted = kinds.flatMap((k) => SUBSCRIPTIONS[k]);
  const scopes = [...new Set(wanted.map((s) => s.scope).filter(Boolean))];
  log(`[Twitch] 購読する種別: ${kinds.join(", ")}`);

  let helix = null;
  let broadcasterId = null;
  if (args.subscribe) {
    const session = await ensureToken(config, scopes, log);
    helix = createHelix(config, session, log, args.apiUrl ? `${args.apiUrl}/helix` : undefined);
    broadcasterId = await helix.userId(config.channel);
    log(`[Twitch] チャンネル ${config.channel} (id ${broadcasterId}) として動きます`);
  }

  ctx.es = createEventSub({
    url: args.wsUrl,
    log,
    onStatus: (ok, text) => { esConnected = ok; log(`[Twitch] ${text}`); },
    onRevocation: (sub) =>
      log(`[Twitch] 購読が取り消されました: ${sub.type} (${sub.status})。再認証が必要かもしれません`),
    onNotification: onEvent,
    async onWelcome(sessionId) {
      if (!args.subscribe) { log("[Twitch] 購読登録は行いません (--no-subscribe)"); return; }
      subCount = 0;
      for (const s of wanted) {
        const body = {
          type: s.type,
          version: s.version,
          condition: s.cond(broadcasterId),
          transport: { method: "websocket", session_id: sessionId },
        };
        const r = await helix.post("/eventsub/subscriptions", body);
        if (r.status === 202 || r.status === 200 || r.status === 409) subCount++;
        else log(`[Twitch] 購読に失敗: ${s.type} (${r.status}) ${r.body && r.body.message ? r.body.message : ""}`);
      }
      log(`[Twitch] 購読を登録しました: ${subCount}/${wanted.length} 件`);
    },
  });
  ctx.es.start();
}

// ルール表を書き換えたら自動で読み直す (コンパネのボタンを押さなくてよい)
try {
  fs.watchFile(args.rules, { interval: 2000 }, () => reloadRules("ファイルが変更されました"));
} catch (e) { /* 監視できなくても動作には影響しない */ }

main().catch((e) => {
  console.error("[起動できません]", e.message);
  process.exit(1);
});
