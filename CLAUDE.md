# slot_enshutsu_sender — Claude Code 作業ガイド

Twitch 配信用のパチスロ演出オーバーレイ。GOD タイプ機の主制御/副制御を Python で再現し、
WebSocket 中継サーバー経由でオーバーレイ(OBS)・コンパネ・筐体ビューを連携させる。

## フォルダ構成

```
slot_enshutsu_sender/
├── setup.bat                  人手用ワンクリック起動 (依存確認 → サーバー → ブラウザ → 主制御)。Shift-JIS/CRLF
├── run_server.bat             setup.bat から呼ばれる中継サーバー起動用。Shift-JIS/CRLF
├── manual.bat                 人手用: 主制御を手動モードで起動し、メニューでレバーON/クレジット投入/自動切替。Shift-JIS/CRLF
├── package.json               npm scripts (start / dev:* / test / check)
├── server/
│   └── trigger_relay_server.js  WebSocket 中継 + 静的配信 + /api/list + /api/health (port 8787)
├── main_board/
│   ├── god_main_board.py        主制御(MainBoard)・副制御(SubBoard) シミュレータ。--serve で ws://127.0.0.1:8765
│   │                            --manual で「起動しても回さずレバーON待ち」。ベットはMAXベット(3枚)のみ
│   └── reels.json               図柄配列 (1リール21コマ) の唯一の定義。主制御と筐体ビューの両方が読む
├── control/
│   └── main_control.html        コンパネ。演出ボタン・主制御/副制御モニタ・映像配信(WebRTC)・図柄設定(エディタを内蔵)
│                                「主制御 詳細」に遊技操作 (🕹️レバーON / 🪙クレジット投入 / 自動・手動の切替) がある
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
│   ├── real/                    リールの効果音。start (回転開始) / stop (停止。stop1〜stop3 で停止順別も可)
│   │                            筐体ビュー reel.html が /api/list で読む (任意。無ければ無音)
│   ├── at/sound/                AT系演出の効果音 (任意: gg_start / stock_up / add_games / at_end / navi)
│   └── yokoku/
│       ├── freeze/              神揃いフリーズ素材 (cutin/, afterblackout/, frz.mp4, moe.mp4)。GIF/画像に加え動画 (mp4/webm/mov) 可
│       │                        afterblackout/ は mp4 (音声込み) か GIF (無音、設定秒数で表示)。sound/ サブフォルダは廃止
│       └── banner/sound/        予告バナーの効果音 (任意: 白/青/緑/赤/金.mp3)
├── doc/                       仕様書 (主制御・副制御仕様書.docx, スロットの概念.pdf)
├── scripts/
│   ├── dev.ps1 / dev.cmd        CLI 用: start / stop / restart / status / test / open / send / logs
│   ├── ws_send.js               中継サーバーへ JSON を1件送る
│   ├── migrate_layout.bat       旧配置 → 新配置への一回限りの移行 (git mv) + 下記設定の配置
│   └── config/                  .claude/settings.json と .vscode/tasks.json の元ファイル
└── .run/                      dev.ps1 の PID とログ (git 管理外)
```

## 通信経路とポート

- 中継サーバー `ws://127.0.0.1:8787` … コンパネ ⇔ オーバーレイ、主制御モニタ端子 → コンパネ。同じポートで http 静的配信。
- 副制御ポート `ws://127.0.0.1:8765` … god_main_board.py --serve が演出イベントを配信し、オーバーレイが受信。
  副制御の演出トリガーは遊技者の操作に対応する4点: **レバーON** (0x30) / **第1停止** / **第2停止** / **第3停止**
  (0x31〜0x33 の到着順)。レバーONで内部当選 (0x20) から予告プラン `[レバーON, 第1, 第2, 第3]` の各ランクを決め、
  `lever{rank,plan}` を出し、各停止で `stop{n,rank}` を出す (rank=null は演出なし)。神揃いの `freeze` もレバーONで出す。
  主制御は1ゲームぶんのコマンドを一括送出するので lever/stop はほぼ同時に届く。オーバーレイは lever 受信時刻を起点に
  `stopTiming1..3` (既定 1.15/1.6/2.05 秒 = 筐体ビューの停止タイミング) だけ遅らせて停止演出を出す。
  そのほか `navi`(押し順ナビ) / `gg_start` / `stock_up` / `add_games` / `at_end` は状態通知として従来どおり。
  `freeze`・`gg_start`・`at_end` は直列キューで順番に再生する (神揃い時は同一ゲーム内で連続して届くため)。
- URL (すべて 8787 経由で開くこと。file:// で開くと素材フォルダ選択が必要になる)
  - コンパネ     http://localhost:8787/control/main_control.html
  - オーバーレイ http://localhost:8787/enshutsu/enshutsu_overlay.html  (OBS ブラウザソース)
  - 筐体ビュー   http://localhost:8787/reel/reel.html?mode=link&hidebar=1  (`&wait=0` でレバーON無効時間なし)
  - 図柄カタログ http://localhost:8787/reel/symbols.html
  - 図柄設定     http://localhost:8787/reel/symbol_editor.html
  - 旧 URL `/main_control.html` `/kyotai.html` `/kyotai/kyotai.html` はサーバーが 302 で新 URL へ転送する。

## コマンド (Claude Code から実行してよいもの)

```
scripts\dev.cmd start [-Mode normal|fast|tenjo|manual|none]   # 中継サーバー + 主制御をバックグラウンド起動
scripts\dev.cmd status                                 # ポート・health・PID を表示 (exit 0 = サーバー稼働中)
scripts\dev.cmd test                                   # 主制御 2000G / 副制御 300 イベント / JS 構文チェック
scripts\dev.cmd send triggerEnshutsu                  # コンパネのボタンと同じメッセージを送る (JSON 直指定も可)
scripts\dev.cmd send '{"action":"subEvent","event":{"type":"banner","rank":"赤"}}'   # 副制御イベントをオーバーレイへ直送
scripts\dev.cmd send reelIn                          # リールユニットを液晶(オーバーレイ)内に入れる (reelOut / reelToggle も可)
scripts\dev.cmd send lever                           # -Mode manual の主制御を1ゲーム進める (レバーON)
scripts\dev.cmd send credit                          # クレジット投入信号 +50枚 (send manual / send auto で進み方の切替)
scripts\dev.cmd logs                                   # .run\*.log の末尾
scripts\dev.cmd stop
npm test / npm run check / npm start                   # 同等の npm scripts
```

- Git Bash から呼ぶ場合は `./scripts/dev.cmd start` または `powershell -ExecutionPolicy Bypass -File scripts/dev.ps1 start`。
- `-Mode fast` (0.5 秒/G・設定 6) は動作確認向け。`normal` は実機ウェイト 4.1 秒/G。
- `-Mode manual` (`--manual --credit 50`) は**起動しても勝手に回さない**。1ゲームずつレバーON入力を待つ。
  入力はコンパネ「主制御 詳細」の🕹️レバーON、筐体ビューの Space/Enter (連動中)、端末起動なら Enter
  (`a`=自動 `m`=手動 `q`=終了)。稼働中でもコンパネの「手動にする/自動にする」で切り替えられる
  (`{action:"panelInject", layer:"mode", manual:true|false}`)。
- ベットは**MAXベット (3枚) のみ**で、回転はMAXベット成立時だけ。クレジットはクレジット投入信号
  (`{action:"panelInject", layer:"credit", n:50}`) で増え、**上限は無い**。払出はすべてクレジットへ入る。
  手動のときはクレジットが3枚未満だとレバーONを叩いても回らない (コンパネの「🪙+3 / +50」で足す)。
  自動 (normal/fast/tenjo や集計モード) はメダルが無限にある台として扱い、不足ぶんは自動で投入する
  (`MainBoard.auto_insert`)。したがって `--ladder` などの集計結果はクレジット導入の前後で変わらない。
- ウェイト (レバーON無効時間): 主制御は `--interval` 秒を「前回の**回転開始**から」計り、明けるまで
  次の遊技を始めない (周期は `max(interval, 実際の遊技時間)`。固定スリープの加算ではない)。
  無効化と復帰の2点だけ `{action:"mainBoard", type:"input", accept, waitMs, reason}` をコンパネへ送り、
  残り時間は受け側が数える。コンパネの「レバー」欄と筐体ビューのリール下の帯がこれを表示する。
  `--freeze-hold 秒` で神揃いフリーズの間さらに回転開始を止められる (既定 0 = 止めない)。
  ウェイトは `--manual` でも生きていて、無効時間中のレバーONは実機と同じく効かない
  (無効時間に入る前に受けたぶん、たとえば `layer:"lever", games:3` は残す)。
  手動で入力待ちの間は `{action:"mainBoard", type:"mode", manual, waiting}` を数秒おきに送り、
  無風でもコンパネの生存監視 (20秒で「受信途絶」) に引っかからないようにしている。
- コード変更後は必ず `scripts\dev.cmd test` を通してから `restart` する。
- 図柄の差し替え: 中継サーバー起動中にコンパネの「図柄設定」→「図柄設定を開く」(または直接
  http://localhost:8787/reel/symbol_editor.html) を開き、カードに画像をドロップして保存。
  サーバーの POST /api/symbols が reel/img/<id>.png と reel/symbol_images.js を書き、WebSocket に symbolsUpdated を流す
  (主制御連動中の筐体ビューは自動再読込)。
- 主制御単体の挙動確認: `py -3 main_board\god_main_board.py --games 2000 --seed 1 --no-panel` (通信なし、集計のみ)。
  `--trace N` で 1G ごとのログ、`--events N` で副制御イベントを JSON 出力。
  `--trace N --manual` は端末で Enter を押すたびに1ゲームだけ進む (`q` で終了)。
- 設定差の確認: `py -3 main_board\god_main_board.py --ladder` (設定1〜6の機械割を並べ、逆転があれば警告。
  既定 500万G/設定・約10秒、マルチプロセス)。抽選テーブル (`LOTTERY_TABLE` / `LOTTERY_TABLE_AT`) を
  変更したら必ず通す。単発の `--games 10000` は±10%以上ぶれるので設定差の判断には使えない。

## 編集時の注意

- `setup.bat` / `run_server.bat` / `manual.bat` / `scripts\dev.cmd` は **Shift-JIS (cp932) + CRLF**。UTF-8 で保存すると
  日本語が文字化けし、`choice` や `echo` が壊れる。編集後は文字コードを必ず確認する。
- `enshutsu_overlay.html` は自身の URL から素材フォルダ (`enshutsu/yokoku/freeze/...`, `enshutsu/at/sound/` など) を
  `/api/list` で解決する。オーバーレイと素材フォルダの相対位置を変えないこと。
- オーバーレイの HUD (バナー・ナビ・ポップアップ) の文字サイズは CSS 変数 `--sh` (16:9 ステージの高さ) 比で指定する。
  px 固定にしない (OBS の解像度に依存させない)。スロー再生は `--spd` でトランジション時間にも効く。
- 設定パネル (歯車) の「予告」「AT」タブに各演出のテストボタンがある。本物のイベントと同じ `handleSubEvent` を通る。
- 筐体ビューのウェイトは `reel.html` の「ウェイト」ブロック (`armWait` / `acceptsLever` / `renderWait`)。
  ローカル試打は自前に計ってレバーONを弾き、主制御連動では `type:"input"` を監視して表示するだけ。
  `renderWait` は**即時実行**のアニメーションループから毎フレーム呼ばれるので、このブロックを
  ループより後ろへ動かさないこと (const の TDZ でスクリプト全体が止まる)。
  なお表示していないタブでは rAF ごと停止するため、動作確認はタブを見える状態にして行う。
- リールの効果音は筐体ビュー側 (`reel/reel.html`) が鳴らす。`enshutsu/real/` を `/api/list` で探し、回転開始で
  `start` を1回、各リール停止で `stop`(停止順に分けるなら `stop1`/`stop2`/`stop3`) を鳴らす。ファイルが無ければ無音。
  音量は単体なら `?vol=`、オーバーレイ内なら設定の `sfxVolume` を `postMessage({type:"reelSound"})` で渡している。
  iframe 内で鳴らすため `#reel-frame` の `allow="autoplay"` を外さないこと。
- 「リール」タブ: 筐体ビュー `reel/reel.html?mode=link&hidebar=1` を iframe (`#reel-frame`) で液晶内に埋め込み、
  `#reel-layer.in` で下からスライドして出し入れする。位置・幅は % 指定 (`reelX/reelY/reelW`)、状態は `reelIn` として保存。
  筐体ビューは自分で 8787 に接続して主制御の state でリールを回すので、オーバーレイ側は表示位置と出し入れだけを持つ。
  オーバーレイと reel/ の相対位置 (`../reel/`) を変えないこと。
- 動画素材: `afterblackout/` と `cutin/` は GIF/画像と同じ扱いで mp4/webm/mov を置ける (`assetRecord` の `kind` で分岐)。
  `afterblackout/` の音声は動画に埋め込む (別ファイルの `sound/` は廃止済み)。GIF は無音で `freezeGifDuration` 秒表示する。
  固定素材は `freeze/frz.webm|mp4`・`freeze/moe.webm|mp4` があれば GIF より優先 (`probeVideoVariant`)。
  萌えカットインの重ね演出 `moe.mp4` とロック3の暗転つなぎ `frz.mp4` は映像と音声を1本にした動画で、音声込みで
  1回再生する (moecut.mp3 / blackout.mp3 は廃止済み。moe.gif / frz.gif は動画が無いときの無音の代替)。
  `<video>` の再生は必ず `startVideo` / `stopVideo` を通す (src 変更直後の play() は Chrome で失敗することがあるため
  `loadedmetadata` を待ってから再生している)。静的配信は Range (206) / Last-Modified (304) 対応済みなので、動画の
  巻き戻し・シークはサーバー側で完結する。
- コンパネの骨格: `body` を縦フレックスにし、ヘッダ(接続) → 未接続ヘルプ → 主制御ダイジェスト(`.strip`) →
  タブ(`.tabs`) → 面(`.panes`) → ログ(`.logbar`) を積む。スクロールするのは `.panes` だけで、ダイジェストと
  ログはどのタブでも見えたまま。面は「操作 / モニタ / 設定 / 注入」の4枚 (`.pane` を `.on` で切り替え)。
  setup.bat と dev.ps1 が 480×900 のウィンドウで開くので、要素を足すときはこの幅と高さで収まるか確かめる。
  1200px 以上ではモニタ面を右列に常設し、そのタブは CSS で隠す (JS の `activatePane` が対で逃がす)。
  イベント表示 (`mb-events` / `mb-cmds` / `sb-events`) は隠れている間スクロールが追従しないので、
  面を出すときに末尾へ送っている。
- `main_control.html` は相対パス依存なし。接続先は画面内の ws URL 入力欄 (既定 ws://localhost:8787)。
  「図柄設定」カードは `reel/symbol_editor.html` を全画面モーダルの iframe で開くが、その URL も ws URL の
  ホストから組み立てる (ws URL が読めないときだけ `location.origin` を使う)。相対パスを書かないこと。
  保存結果は中継サーバーが流す `symbolsUpdated` を受けてカードとログに出す。
- 図柄と配列: 図柄は `reel/symbols.js` に SVG として定義し、`reel.html` / `symbols.html` はスプライトを `<svg><use>` で
  参照する (1リール21コマ + 継ぎ目複製で 26要素 × 3リール)。配列は `main_board/reels.json` が唯一の定義で、主制御は
  起動時に読み、筐体ビューは `../main_board/reels.json` を fetch する (file:// では読めないので 8787 経由で開く)。
  図柄を増減したら `symbols.js` の INFO / BODY と `reels.json` を直し、`symbols.html` で見た目を確認する。
- 主制御 → 副制御は 2 バイトコマンド (単方向)。副制御は主制御の内部状態を直接見ない。この境界を守る
  (仕様は doc/主制御・副制御仕様書.docx)。
- 中継サーバーはメッセージを「受信したら他の全クライアントへ転送するだけ」。ロジックを足さない。
- 素材 (gif/mp3/wav/jpg) は大きい。バイナリを差し替えるコミットは分ける。

## 起動の前提 (人手)

- manual.bat をダブルクリックすると主制御を手動モード (`-Mode manual`) で起動し、キー1つでレバーON・クレジット投入・
  自動/手動の切替・状態表示・停止ができる (中身は `scripts\dev.cmd start/send/status/stop` の呼び出しだけ)。
- setup.bat をダブルクリックすれば依存インストールから全起動まで行う。dev.ps1 と同時に使うとポート競合の警告が出るが問題ない
  (dev.ps1 の stop はポートからも探して止める)。起動モードの選択肢は `[1] 通常 [2] 高速 [3] 天井 [4] 手動 [5] 起動しない`
  で、`-Mode` と対応している (`[4] 手動` = `--manual --credit 50`)。
- Node.js と Python 3 (`py -3` または `python`) が PATH にあること。ws は `npm install` で入る。
- `.claude/settings.json` (Claude Code の許可コマンド) と `.vscode/tasks.json` は `scripts/config/` から
  `scripts\migrate_layout.bat` が配置する。変更するときは `scripts/config/` 側も同じ内容にしておく。
