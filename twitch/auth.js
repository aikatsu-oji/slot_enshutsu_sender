// Twitch の認証まわり (Device Code Grant とトークン更新、Helix 呼び出し)
//
// 配信 PC のローカルで完結させたいので、リダイレクト用の HTTP サーバーを立てなくてよい
// Device Code Grant を使う。ブラウザに 8 桁のコードを出して承認してもらうと
// リフレッシュトークンが手に入り、以後は自動更新される。
//
// 設定とトークンの置き場所 (どちらも .gitignore 済みの .run/ 配下):
//   .run/twitch_config.json  { "clientId": "...", "channel": "..." }
//   .run/twitch_token.json   Device Code フローの結果 (触らない)
// 環境変数 TWITCH_CLIENT_ID / TWITCH_CHANNEL があればそちらが優先される。
//
// ※ エンドポイントとスコープ名は Twitch 側の改訂があるため、動かないときは
//    https://dev.twitch.tv/docs/authentication/ で最終確認すること。

const fs = require("fs");
const path = require("path");

const ROOT = path.resolve(__dirname, "..");
const RUN_DIR = path.join(ROOT, ".run");
const CONFIG_PATH = path.join(RUN_DIR, "twitch_config.json");
const TOKEN_PATH = path.join(RUN_DIR, "twitch_token.json");

const ID_BASE = "https://id.twitch.tv/oauth2";
const HELIX_BASE = "https://api.twitch.tv/helix";

// Node 18 未満には fetch が無い。ここで止めておかないと分かりにくいエラーになる。
if (typeof fetch !== "function") {
  throw new Error("Node.js 18 以降が必要です (グローバル fetch を使います)");
}

function readJson(file) {
  try {
    return JSON.parse(fs.readFileSync(file, "utf-8"));
  } catch (e) {
    return null;
  }
}

function writeJson(file, obj) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, JSON.stringify(obj, null, 2) + "\n");
}

// 設定を読む。環境変数 → .run/twitch_config.json の順で見る。
function loadConfig() {
  const file = readJson(CONFIG_PATH) || {};
  const clientId = process.env.TWITCH_CLIENT_ID || file.clientId || "";
  const channel = process.env.TWITCH_CHANNEL || file.channel || "";
  if (!clientId || !channel) {
    throw new Error(
      "clientId と channel が設定されていません。\n" +
        "  twitch/config.example.json を .run/twitch_config.json にコピーして埋めるか、\n" +
        "  環境変数 TWITCH_CLIENT_ID / TWITCH_CHANNEL を設定してください。"
    );
  }
  return { clientId, channel: String(channel).toLowerCase() };
}

async function postForm(url, params) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams(params).toString(),
  });
  let body = null;
  try {
    body = await res.json();
  } catch (e) {
    /* 本文が空のこともある */
  }
  return { status: res.status, body };
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// --- Device Code Grant -----------------------------------------------------
// 1. /device でコードを取り、視聴者ではなく配信者本人にブラウザで承認してもらう
// 2. /token を interval 秒ごとに叩き、承認されるまで待つ
async function deviceCodeFlow(clientId, scopes, log) {
  const start = await postForm(`${ID_BASE}/device`, {
    client_id: clientId,
    scopes: scopes.join(" "),
  });
  if (start.status !== 200 || !start.body || !start.body.device_code) {
    throw new Error(`デバイスコードの取得に失敗しました (${start.status}): ${JSON.stringify(start.body)}`);
  }
  const { device_code, user_code, verification_uri, interval = 5, expires_in = 1800 } = start.body;

  log("");
  log("  ┌─────────────────────────────────────────────");
  log("  │ ブラウザで次の URL を開き、コードを入力してください");
  log(`  │   URL : ${verification_uri}`);
  log(`  │   コード: ${user_code}`);
  log("  └─────────────────────────────────────────────");
  log("");

  const deadline = Date.now() + expires_in * 1000;
  let wait = Math.max(1, Number(interval)) * 1000;
  while (Date.now() < deadline) {
    await sleep(wait);
    const r = await postForm(`${ID_BASE}/token`, {
      client_id: clientId,
      device_code,
      grant_type: "urn:ietf:params:oauth:grant-type:device_code",
      scopes: scopes.join(" "),
    });
    if (r.status === 200 && r.body && r.body.access_token) return r.body;
    const msg = (r.body && (r.body.message || r.body.error)) || "";
    if (/slow_down/i.test(msg)) {
      wait += 5000;
      continue;
    }
    if (/authorization_pending|pending/i.test(msg)) continue;
    throw new Error(`承認に失敗しました (${r.status}): ${msg || JSON.stringify(r.body)}`);
  }
  throw new Error("承認がタイムアウトしました。もう一度実行してください。");
}

// アクセストークンの生存確認。ついでに broadcaster の user_id も取れる。
async function validate(accessToken) {
  const res = await fetch(`${ID_BASE}/validate`, {
    headers: { Authorization: `OAuth ${accessToken}` },
  });
  if (res.status !== 200) return null;
  return res.json();
}

async function refreshToken(clientId, refresh_token) {
  const r = await postForm(`${ID_BASE}/token`, {
    client_id: clientId,
    grant_type: "refresh_token",
    refresh_token,
  });
  if (r.status !== 200 || !r.body || !r.body.access_token) {
    throw new Error(`トークンの更新に失敗しました (${r.status}): ${JSON.stringify(r.body)}`);
  }
  return r.body;
}

/**
 * 使えるアクセストークンを返す。
 * 保存済みトークン → 生存確認 → 切れていればリフレッシュ → それも駄目なら Device Code フロー。
 */
async function ensureToken(config, scopes, log) {
  const saved = readJson(TOKEN_PATH);

  if (saved && saved.access_token) {
    const v = await validate(saved.access_token);
    if (v && v.client_id === config.clientId) {
      return { accessToken: saved.access_token, login: v.login, userId: v.user_id, scopes: v.scopes || [] };
    }
    if (saved.refresh_token) {
      log("[Twitch] トークンを更新します");
      try {
        const fresh = await refreshToken(config.clientId, saved.refresh_token);
        writeJson(TOKEN_PATH, fresh);
        const v2 = await validate(fresh.access_token);
        if (v2) return { accessToken: fresh.access_token, login: v2.login, userId: v2.user_id, scopes: v2.scopes || [] };
      } catch (e) {
        log(`[Twitch] 更新に失敗しました: ${e.message}`);
      }
    }
  }

  log("[Twitch] 認証が必要です (初回、またはトークンが失効しています)");
  const token = await deviceCodeFlow(config.clientId, scopes, log);
  writeJson(TOKEN_PATH, token);
  const v = await validate(token.access_token);
  if (!v) throw new Error("取得したトークンの生存確認に失敗しました");
  log(`[Twitch] 認証できました: ${v.login}`);
  return { accessToken: token.access_token, login: v.login, userId: v.user_id, scopes: v.scopes || [] };
}

/**
 * Helix を叩く。401 を受けたら 1 回だけリフレッシュして再試行する。
 * apiBase を差し替えると Twitch CLI のモックサーバーへ向けられる。
 */
function createHelix(config, session, log, apiBase = HELIX_BASE) {
  async function call(method, pathname, body) {
    const doFetch = () =>
      fetch(`${apiBase}${pathname}`, {
        method,
        headers: {
          "Client-Id": config.clientId,
          Authorization: `Bearer ${session.accessToken}`,
          "Content-Type": "application/json",
        },
        body: body ? JSON.stringify(body) : undefined,
      });

    let res = await doFetch();
    if (res.status === 401) {
      const saved = readJson(TOKEN_PATH);
      if (saved && saved.refresh_token) {
        log("[Twitch] 401 を受けたのでトークンを更新して再試行します");
        const fresh = await refreshToken(config.clientId, saved.refresh_token);
        writeJson(TOKEN_PATH, fresh);
        session.accessToken = fresh.access_token;
        res = await doFetch();
      }
    }
    let json = null;
    try {
      json = await res.json();
    } catch (e) {
      /* 204 など本文なし */
    }
    return { status: res.status, body: json };
  }

  return {
    get: (p) => call("GET", p),
    post: (p, b) => call("POST", p, b),
    // 配信者のユーザー ID を login 名から引く
    async userId(login) {
      const r = await call("GET", `/users?login=${encodeURIComponent(login)}`);
      const u = r.body && r.body.data && r.body.data[0];
      if (!u) throw new Error(`チャンネルが見つかりません: ${login} (${r.status})`);
      return u.id;
    },
  };
}

module.exports = { loadConfig, ensureToken, createHelix, CONFIG_PATH, TOKEN_PATH };
