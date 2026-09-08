# slot_enshutsu_sender — Claude Code 作業ガイド

Twitch 配信用のパチスロ演出オーバーレイ。GOD タイプ機の主制御/副制御を Python で再現し、
WebSocket 中継サーバー経由でオーバーレイ(OBS)・コンパネ・筐体ビューを連携させる。

## フォルダ構成

```
slot_enshutsu_sender/
├── setup.bat                  人手用ワンクリック起動 (依存確認 → サーバー → ブラウザ → 主制御)。Shift-JIS/CRLF
├── run_server.bat             setup.bat から呼ばれる中継サーバー起動用。Shift-JIS/CRLF
├── package.json               npm scripts (start / dev:* / test / check)
├── server/
│   └── trigger_relay_server.js  WebSocket 中継 + 静的配信 + /api/list + /api/health (port 8787)
├── main_board/
│   ├── god_main_board.py        主制御(MainBoard)・副制御(SubBoard) シミュレータ。--serve で ws://127.0.0.1:8765
│   └── reels.json               図柄配列 (1リール21コマ) の唯一の定義。主制御と筐体ビューの両方が読む
├── control/
│   └── main_control.html        コンパネ。演出ボタン・主制御/副制御モニタ・映像配信(WebRTC)・図柄設定(エディタを内蔵)
├── reel/
│   ├── reel.html                筐体ビュー(リールユニットのみ)。?mode=link で主制御と連動
│   ├── symbols.js               図柄定義 (SVG スプライト、viewBox 240×80)。globalThis.SlotSymbols。画像がある図柄は <image>
│   ├── symbol_images.js         画像図柄の data URI と設定 (地色/背景画像/枠の色と透過・枠の太さ/間隔/角丸・上下の影・図柄の大きさ)。symbol_editor.html の保存で生成 (手で編集しない)
│   ├── symbol_editor.html       図柄設定。画像のドロップ/貼り付け → 背景透過・切り抜き → POST /api/symbols で保存
│   ├── img/<id>.png             図柄の元画像 (切り抜き済み透過 PNG)。god/seven/bell/rep/melon/blank。reel_bg.png はリール背景画像の元
│   ├── reel_window.js           リール窓の DOM 構築・停止位置描画 (ReelView)、reels.json の読込。globalThis.SlotReels
│   └── symbols.html             図柄カタログ。全図柄・リール窓の停止形・配列表の確認と SVG/PNG 書き出し
├── enshutsu/
│   ├── enshutsu_overlay.html    OBS ブラウザソース用オーバーレイ本体
│   ├── real/                    実機系素材 (start.wav など)
│   ├── at/sound/                AT系演出の効果音 (任意: gg_start / stock_up / add_games / at_end / navi)
│   └── yokoku/
│       ├── freeze/              神揃いフリーズ素材 (cutin/, afterblackout/, blackout.mp3 ...)。GIF/画像に加え動画 (mp4/webm/mov) 可
│       │                        afterblackout/ は mp4 (音声込み) か GIF (無音、設定秒数で表示)。sound/ サブフォルダは廃止
│       └── banner/sound/        予告バナーの効果音 (任意: 白/青/緑/赤/金.mp3)
├── twitch/                    Twitch 連携 (いまはチャット連帯カウンタだけ。ほかの要素は後々設計する)
│   ├── twitch_bridge.js         チャット/EventSub → 正規化 → ルール判定 → 流量制御 → 中継サーバーへ。--no-medals で演出だけに戻せる
│   ├── chat_irc.js              チャットを匿名で読む (IRC over WebSocket)。既定の経路。認証不要
│   ├── config.js                設定 (channel / clientId) の読み取りだけ。認証を通らずに使える
│   ├── eventsub.js              EventSub WebSocket の薄い層。チャット以外のルールを足したときだけ通る
│   ├── auth.js                  Device Code Grant とトークン更新。同上。トークンは .run/ 配下 (git 管理外)
│   ├── rules.json               イベント → 操作の対応表。人が編集する唯一の設定ファイル
│   ├── config.example.json      .run/twitch_config.json のひな形 (channel / clientId)
│   └── mock_events.jsonl        Twitch に繋がずに全経路を通すテストデータ (30コメントで連帯カウンタが埋まる)
├── doc/                       仕様書 (主制御・副制御仕様書.docx, スロットの概念.pdf, twitch連携設計.md,
│                              twitch認証の取り方.md, はじめて使う人へ.md)
├── scripts/
│   ├── dev.ps1 / dev.cmd        CLI 用: start / stop / restart / status / test / open / send / logs
│   ├── ws_send.js               中継サーバーへ JSON を1件送る
│   ├── migrate_layout.bat       旧配置 → 新配置への一回限りの移行 (git mv) + 下記設定の配置
│   └── config/                  .claude/settings.json と .vscode/tasks.json の元ファイル
└── .run/                      dev.ps1 の PID とログ (git 管理外)
```

## 通信経路とポート

- 中継サーバー `ws://127.0.0.1:8787` … コンパネ ⇔ オーバーレイ、主制御モニタ端子 → コンパネ。同じポートで http 静的配信。
  **既定でループバック (127.0.0.1) だけを待ち受ける。** 遊技者の操作も流れる経路なので、同一 LAN から
  叩かれないようにするため。別 PC の OBS から見るときだけ `HOST=0.0.0.0` を付けて起動する (起動時に警告が出る)。
  コンパネ・オーバーレイ・筐体ビューは、http:// 経由で開かれていれば接続先をそのページの URL から決める。
- 副制御ポート `ws://127.0.0.1:8765` … god_main_board.py --serve が演出イベントを配信し、オーバーレイが受信。
  オーバーレイは `banner`(予告バナー) / `navi`(押し順ナビ) / `freeze`(ロック1→2→3) / `gg_start` / `stock_up` /
  `add_games` / `at_end` を表示する。`freeze`・`gg_start`・`at_end` は直列キューで順番に再生する
  (神揃い時は同一ゲーム内で freeze → stock_up → gg_start が連続して届くため)。
- URL (すべて 8787 経由で開くこと。file:// で開くと素材フォルダ選択が必要になる)
  - コンパネ     http://localhost:8787/control/main_control.html
  - オーバーレイ http://localhost:8787/enshutsu/enshutsu_overlay.html  (OBS ブラウザソース)
  - 筐体ビュー   http://localhost:8787/reel/reel.html?mode=link&hidebar=1
  - 図柄カタログ http://localhost:8787/reel/symbols.html
  - 図柄設定     http://localhost:8787/reel/symbol_editor.html
  - 旧 URL `/main_control.html` `/kyotai.html` `/kyotai/kyotai.html` はサーバーが 302 で新 URL へ転送する。

## コマンド (Claude Code から実行してよいもの)

```
scripts\dev.cmd start [-Mode normal|fast|tenjo|none] [-Credit]   # 中継サーバー + 主制御をバックグラウンド起動
                                                       # -Credit でクレジット制 (メダルが尽きたら待機)
scripts\dev.cmd status                                 # ポート・health・PID を表示 (exit 0 = サーバー稼働中)
scripts\dev.cmd test                                   # 主制御 2000G / 副制御 300 イベント / JS 構文チェック
scripts\dev.cmd send triggerEnshutsu                  # コンパネのボタンと同じメッセージを送る (JSON 直指定も可)
scripts\dev.cmd send '{"action":"subEvent","event":{"type":"banner","rank":"赤"}}'   # 副制御イベントをオーバーレイへ直送
scripts\dev.cmd send reelIn                          # リールユニットを液晶(オーバーレイ)内に入れる (reelOut / reelToggle も可)
scripts\dev.cmd logs                                   # .run\*.log の末尾
scripts\dev.cmd stop
npm test / npm run check / npm start                   # 同等の npm scripts
npm run twitch:mock                                    # Twitch に繋がず擬似イベントを流す (中継サーバーが要る)
node twitch/twitch_bridge.js --chat <channel>          # 本番。匿名でチャットを読む。認証もアプリ登録も不要
npm run twitch                                         # 同上 (チャンネル名は TWITCH_CHANNEL か .run/twitch_config.json)
```

- Git Bash から呼ぶ場合は `./scripts/dev.cmd start` または `powershell -ExecutionPolicy Bypass -File scripts/dev.ps1 start`。
- `-Mode fast` (0.5 秒/G・設定 6) は動作確認向け。`normal` は実機ウェイト 4.1 秒/G。
- コード変更後は必ず `scripts\dev.cmd test` を通してから `restart` する。
- 図柄の差し替え: 中継サーバー起動中にコンパネの「図柄設定」→「図柄設定を開く」(または直接
  http://localhost:8787/reel/symbol_editor.html) を開き、カードに画像をドロップして保存。
  サーバーの POST /api/symbols が reel/img/<id>.png と reel/symbol_images.js を書き、WebSocket に symbolsUpdated を流す
  (主制御連動中の筐体ビューは自動再読込)。
- 主制御単体の挙動確認: `py -3 main_board\god_main_board.py --games 2000 --seed 1 --no-panel` (通信なし、集計のみ)。
  `--trace N` で 1G ごとのログ、`--events N` で副制御イベントを JSON 出力。
- クレジット制の確認: `py -3 main_board\god_main_board.py --credit --credit-init 30 --no-bank --games 200 --no-panel --seed 1`
  (投入 → 消化 → 尽きたら終了)。`--serve` と併せると待機状態がコンパネに出る。

## 編集時の注意

- `setup.bat` / `run_server.bat` / `scripts\dev.cmd` は **Shift-JIS (cp932) + CRLF**。UTF-8 で保存すると
  日本語が文字化けし、`choice` や `echo` が壊れる。編集後は文字コードを必ず確認する。
- `enshutsu_overlay.html` は自身の URL から素材フォルダ (`enshutsu/yokoku/freeze/...`, `enshutsu/at/sound/` など) を
  `/api/list` で解決する。オーバーレイと素材フォルダの相対位置を変えないこと。
- オーバーレイの HUD (バナー・ナビ・ポップアップ) の文字サイズは CSS 変数 `--sh` (16:9 ステージの高さ) 比で指定する。
  px 固定にしない (OBS の解像度に依存させない)。スロー再生は `--spd` でトランジション時間にも効く。
- 設定パネル (歯車) の「予告」「AT」タブに各演出のテストボタンがある。本物のイベントと同じ `handleSubEvent` を通る。
- 「視聴者」タブ: クレジット制のときの視聴者 HUD (クレジット/下皿/貯金・天井までの回転数・
  いま誰のメダルか・投入ランキング TOP3・チャット連帯ゲージ)、待機中の「メダル募集中」、投入トースト。
  中継サーバー(8787)の `mainBoard`(state/idle/player) と `twitchState` を見て表示するだけで、
  遊技へ戻る経路は無い。主制御が `--credit` でないとき (credit が来ない) は HUD ごと自動的に隠れる。
  名前は必ず `textContent` で入れる (`innerHTML` を使わない)。
- コンパネの「Twitch 連携」カードは `twitchState` (1秒周期) を見てランプと数値を出し、
  キルスイッチ (`twitchControl`)・ルール表の再読込・疑似イベント (`twitchMock`) を送る。
  ブリッジが居なくても静かに待つだけで、他の機能には影響しない。
  視聴者名はブリッジ側でサニタイズ済みだが、コンパネでも必ずエスケープしてから DOM に入れる。
- 「リール」タブ: 筐体ビュー `reel/reel.html?mode=link&hidebar=1` を iframe (`#reel-frame`) で液晶内に埋め込み、
  `#reel-layer.in` で下からスライドして出し入れする。位置・幅は % 指定 (`reelX/reelY/reelW`)、状態は `reelIn` として保存。
  筐体ビューは自分で 8787 に接続して主制御の state でリールを回すので、オーバーレイ側は表示位置と出し入れだけを持つ。
  オーバーレイと reel/ の相対位置 (`../reel/`) を変えないこと。
- 動画素材: `afterblackout/` と `cutin/` は GIF/画像と同じ扱いで mp4/webm/mov を置ける (`assetRecord` の `kind` で分岐)。
  `afterblackout/` の音声は動画に埋め込む (別ファイルの `sound/` は廃止済み)。GIF は無音で `freezeGifDuration` 秒表示する。
  固定素材は `freeze/frz.webm|mp4`・`freeze/moe.webm|mp4` があれば GIF より優先 (`probeVideoVariant`)。
  `<video>` の再生は必ず `startVideo` / `stopVideo` を通す (src 変更直後の play() は Chrome で失敗することがあるため
  `loadedmetadata` を待ってから再生している)。静的配信は Range (206) / Last-Modified (304) 対応済みなので、動画の
  巻き戻し・シークはサーバー側で完結する。
- `main_control.html` は相対パス依存なし。接続先は画面内の ws URL 入力欄 (既定 ws://localhost:8787)。
  「図柄設定」カードは `reel/symbol_editor.html` を全画面モーダルの iframe で開くが、その URL も ws URL の
  ホストから組み立てる (ws URL が読めないときだけ `location.origin` を使う)。相対パスを書かないこと。
  保存結果は中継サーバーが流す `symbolsUpdated` を受けてカードとログに出す。
- 図柄と配列: 図柄は `reel/symbols.js` に SVG として定義し、`reel.html` / `symbols.html` はスプライトを `<svg><use>` で
  参照する (1リール21コマ + 継ぎ目複製で 26要素 × 3リール)。配列は `main_board/reels.json` が唯一の定義で、主制御は
  起動時に読み、筐体ビューは `../main_board/reels.json` を fetch する (file:// では読めないので 8787 経由で開く)。
  図柄を増減したら `symbols.js` の INFO / BODY と `reels.json` を直し、`symbols.html` で見た目を確認する。
- Twitch 連携 (`twitch/`) は中継サーバーに 1 クライアントとして繋ぐだけ。中継サーバーは改造しない。
  演出は `{"action":"subEvent","event":{...}}` で送る (オーバーレイが 8787 で直接受ける)。
  `panelInject(layer:"enshutsu")` は主制御を経由するので、主制御が起動していないと届かない。
  メダルは `{"action":"playerInput","input":"insertMedal","medals":N,"src":"..."}` で送る。
  **抽選に触る口は既定で閉じている。** ブリッジが送れるのは投入・押し順・一時停止・電源だけ。
  強制フラグ (`forceFlag` = イベント用のやらせ) は **ブリッジと主制御の両方に `--allow-force`**
  を付けたときだけ効き、使った回数は `forced_games` に残って集計から切り分けられる。
  `requires:"mod"` はチャットのバッジ (`ev.user.isMod`) で判定する。
  ルールに `"enabled": false` を書くとそのルールだけ無効にできる。
  1分あたりの上限に当たったぶんは捨てずに待たせる (視聴者が押したぶんを失わない)。
  `--no-medals` で演出だけの挙動 (段階1) に戻せる。
- **いまのルール表はチャット連帯カウンタ (30コメント → 120枚) だけ。ほかの要素は後々設計する。**
  初コメ歓迎・設定変更・押し順投票は `"enabled": false` で置いてある (チャットなので有効に
  してもよい)。レイド・ビッツ・サブスク・フォロー・チャンネルポイントのルールは削除した。
  各イベントの設計 (投入枚数・バナーのランク表) は `doc/twitch連携設計.md` に残っている。
- **クライアント ID は同梱しない。既定の構成では認証もアプリ登録も要らない。**
  有効なルールがチャットだけなら (`chatOnlyRules()`)、ブリッジは匿名 IRC (`chat_irc.js` /
  justinfan) を選ぶ。必要なのはチャンネル名だけで、`--chat <channel>` → 環境変数
  `TWITCH_CHANNEL` → `.run/twitch_config.json` の `channel` の順に見る。
  設定の読み取りは `config.js` に分けてある (チャット連動で `auth.js` を通らないようにするため)。
- チャット以外のルールを 1 つでも有効にすると EventSub 側 (要認証) に切り替わり、
  **各自で登録したアプリの clientId** が要る (`ensureToken` の `requireClientId` で弾く)。
  clientId とトークンは `.run/` 配下 (git 管理外)。リポジトリに入れない。
  手順は `doc/twitch認証の取り方.md`。要求スコープはルール表から自動で決まる。
- **チャンネルポイントは使わない** (アフィリエイト/パートナー限定の機能で、未到達だと存在しない。
  実接続で `Get Custom Reward` が 403 になって判明した)。
  `checkRewards` は `kind:"redeem"` のルールがあるときだけ走る (読み取りのみ)。
  403 は「アフィリエイト未到達」を疑わせるメッセージを出す。
  200 のときは NFKC + 空白除去で似た名前を突き止めて出す (名前が1文字違うと無反応になるため)。
- チャット連動では `stream.online` が取れないので、連帯カウンタ・初コメ判定・ランキングの
  リセット点は**ブリッジの起動時**になる (配信ごとに起動し直す)。起動ログにそう出す。
- 普通のコメントは中継へ流さない (本文を出さない)。**連帯カウンタの達成だけは
  `twitchEvent`(`kind:"counter"`) で流す。**これが無いとコンパネのイベント欄が
  ずっと空のままで、動いているのか分からない。
- 投入の流量制御 (`createMedalQueue`) の**トークンバケツは空ではなく 10 分ぶんの残高から始める**
  (`INITIAL_MINUTES`)。空だと 120枚 ÷ 14枚/分 = 8.6 分ぶん貯まるまで最初の 1 回が必ず待たされ、
  「達成したのにメダルが入らない」に見える (実際に踏んだ)。上限に当たって待たせるときは
  ログに待ち時間の目安を出す。**上限を超えたぶんは捨てない。**
- **ルール表の正規表現は必ず `u` フラグで組む。** `u` が無いと `\p{L}` が使えないうえ、
  `\W` が日本語を「単語でない文字」とみなすため、日本語のコメントが全部除外されてしまう
  (連帯カウンタが一度も溜まらなくなる。実際に踏んだ)。
- クレジット制 (`--credit`) は主制御の既定では OFF。付けたときだけ「メダルが 3 枚以上あるときだけ回る」。
  `credit`(上限50) → `reserve`(下皿・上限3000) → `bank`(貯金) の 3 段で、溢れた分は
  `.run/twitch_bank.json` に書いて次回配信へ持ち越す (10秒ごとに保存するので強制終了でも残る)。
  「誰のメダルで回っているか」は `credit_src` の FIFO で追うが、**表示用の付帯情報でしかない**。
  8787 のモニタ端子にだけ出し、2バイトコマンド (8765) には絶対に乗せない。
- 押し順は 0-5 の番号。**0,1 = 左第一停止 / 2,3 = 中第一停止 / 4,5 = 右第一停止**。
  `playerInput / stopOrder` で次ゲームぶんだけ指定でき、AT 中に指定するとナビを無視して押すことになる
  (押順ベルの正解率が 1/6 に落ちて損をする)。指定が無ければ AT 中はナビ通り、通常時はランダム押し。
- 主制御 → 副制御は 2 バイトコマンド (単方向)。副制御は主制御の内部状態を直接見ない。この境界を守る
  (仕様は doc/主制御・副制御仕様書.docx)。
- 中継サーバーはメッセージを「受信したら他の全クライアントへ転送するだけ」。ロジックを足さない。
- 素材 (gif/mp3/wav/jpg) は大きい。バイナリを差し替えるコミットは分ける。
  `enshutsu/` の同梱素材は**作者個人のもの**。第三者が使うときは差し替える前提で、
  素材が 1 つも無くてもその演出をスキップして動くようになっている (空フォルダで確認済み)。
  第三者向けの導入手順は `doc/はじめて使う人へ.md`。

## 起動の前提 (人手)

- setup.bat をダブルクリックすれば依存インストールから全起動まで行う。dev.ps1 と同時に使うとポート競合の警告が出るが問題ない
  (dev.ps1 の stop はポートからも探して止める)。
- Node.js と Python 3 (`py -3` または `python`) が PATH にあること。ws は `npm install` で入る。
- `.claude/settings.json` (Claude Code の許可コマンド) と `.vscode/tasks.json` は `scripts/config/` から
  `scripts\migrate_layout.bat` が配置する。変更するときは `scripts/config/` 側も同じ内容にしておく。
