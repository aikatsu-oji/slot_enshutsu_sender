// 配布元の Twitch クライアント ID。
//
// **これは秘密情報ではありません。** 公開クライアント (Public) のクライアント ID は
// ブラウザやデスクトップアプリに埋め込まれる前提で設計されていて、これ単体では何もできません。
// 実際の権限はユーザーが承認したときに発行されるトークンが持ち、そのトークンは
// Twitch から利用者の PC へ直接渡ります。**配布元には届きません。**
// (クライアントシークレットは使わない = Device Code Grant + 公開クライアント のため)
//
// ここに値を入れておくと、使う人はアプリ登録が不要になります。
// 入っていない場合は各自で登録が必要 (doc/twitch認証の取り方.md)。
//
// 差し替えたいとき (自分のアプリを使いたいとき) は、どれか 1 つで上書きできます:
//   1. 環境変数 TWITCH_CLIENT_ID
//   2. .run/twitch_config.json の "clientId"
//   3. このファイル
//
// 配布元がここを埋めるときの注意:
//   - 登録するアプリの「クライアントの種類」は必ず **公開 (Public)**
//   - レート制限と EventSub の購読上限は、この ID を使う全員で共有される
//   - このアプリを消すと、この ID を使っている全員が動かなくなる
//   - 要求スコープを増やすと、既存の利用者は承認をやり直す必要がある
(function (root) {
  root.SlotTwitchClientId = "";   // 例: "abcdefghijklmnopqrstuvwxyz1234"
})(typeof globalThis !== "undefined" ? globalThis : this);

module.exports = { DEFAULT_CLIENT_ID: globalThis.SlotTwitchClientId || "" };
