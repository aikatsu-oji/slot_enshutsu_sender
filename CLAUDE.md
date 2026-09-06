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
│   └── god_main_board.py        主制御(MainBoard)・副制御(SubBoard) シミュレータ。--serve で ws://127.0.0.1:8765
├── control/
│   └── main_control.html        コンパネ。演出ボタン・主制御/副制御モニタ・映像配信(WebRTC)
├── kyotai/
│   └── kyotai.html              筐体ビュー(リールユニットのみ)。?mode=link で主制御と連動
├── enshutsu/
│   ├── enshutsu_overlay.html    OBS ブラウザソース用オーバーレイ本体
│   ├── real/                    実機系素材 (start.wav など)
│   ├── at/sound/                AT系演出の効果音 (任意: gg_start / stock_up / add_games / at_end / navi)
│   └── yokoku/
│       ├── freeze/              神揃いフリーズ素材 (cutin/, afterblackout/, blackout.mp3 ...)。GIF/画像に加え動画 (mp4/webm/mov) 可
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
  オーバーレイは `banner`(予告バナー) / `navi`(押し順ナビ) / `freeze`(ロック1→2→3) / `gg_start` / `stock_up` /
  `add_games` / `at_end` を表示する。`freeze`・`gg_start`・`at_end` は直列キューで順番に再生する
  (神揃い時は同一ゲーム内で freeze → stock_up → gg_start が連続して届くため)。
- URL (すべて 8787 経由で開くこと。file:// で開くと素材フォルダ選択が必要になる)
  - コンパネ     http://localhost:8787/control/main_control.html
  - オーバーレイ http://localhost:8787/enshutsu/enshutsu_overlay.html  (OBS ブラウザソース)
  - 筐体ビュー   http://localhost:8787/kyotai/kyotai.html?mode=link&hidebar=1
  - 旧 URL `/main_control.html` `/kyotai.html` はサーバーが 302 で新 URL へ転送する。

## コマンド (Claude Code から実行してよいもの)

```
scripts\dev.cmd start [-Mode normal|fast|tenjo|none]   # 中継サーバー + 主制御をバックグラウンド起動
scripts\dev.cmd status                                 # ポート・health・PID を表示 (exit 0 = サーバー稼働中)
scripts\dev.cmd test                                   # 主制御 2000G / 副制御 300 イベント / JS 構文チェック
scripts\dev.cmd send triggerEnshutsu                  # コンパネのボタンと同じメッセージを送る (JSON 直指定も可)
scripts\dev.cmd send '{"action":"subEvent","event":{"type":"banner","rank":"赤"}}'   # 副制御イベントをオーバーレイへ直送
scripts\dev.cmd send reelIn                          # リールユニットを液晶(オーバーレイ)内に入れる (reelOut / reelToggle も可)
scripts\dev.cmd logs                                   # .run\*.log の末尾
scripts\dev.cmd stop
npm test / npm run check / npm start                   # 同等の npm scripts
```

- Git Bash から呼ぶ場合は `./scripts/dev.cmd start` または `powershell -ExecutionPolicy Bypass -File scripts/dev.ps1 start`。
- `-Mode fast` (0.5 秒/G・設定 6) は動作確認向け。`normal` は実機ウェイト 4.1 秒/G。
- コード変更後は必ず `scripts\dev.cmd test` を通してから `restart` する。
- 主制御単体の挙動確認: `py -3 main_board\god_main_board.py --games 2000 --seed 1 --no-panel` (通信なし、集計のみ)。
  `--trace N` で 1G ごとのログ、`--events N` で副制御イベントを JSON 出力。

## 編集時の注意

- `setup.bat` / `run_server.bat` / `scripts\dev.cmd` は **Shift-JIS (cp932) + CRLF**。UTF-8 で保存すると
  日本語が文字化けし、`choice` や `echo` が壊れる。編集後は文字コードを必ず確認する。
- `enshutsu_overlay.html` は自身の URL から素材フォルダ (`enshutsu/yokoku/freeze/...`, `enshutsu/at/sound/` など) を
  `/api/list` で解決する。オーバーレイと素材フォルダの相対位置を変えないこと。
- オーバーレイの HUD (バナー・ナビ・ポップアップ) の文字サイズは CSS 変数 `--sh` (16:9 ステージの高さ) 比で指定する。
  px 固定にしない (OBS の解像度に依存させない)。スロー再生は `--spd` でトランジション時間にも効く。
- 設定パネル (歯車) の「予告」「AT」タブに各演出のテストボタンがある。本物のイベントと同じ `handleSubEvent` を通る。
- 「リール」タブ: 筐体ビュー `kyotai/kyotai.html?mode=link&hidebar=1` を iframe (`#reel-frame`) で液晶内に埋め込み、
  `#reel-layer.in` で下からスライドして出し入れする。位置・幅は % 指定 (`reelX/reelY/reelW`)、状態は `reelIn` として保存。
  筐体ビューは自分で 8787 に接続して主制御の state でリールを回すので、オーバーレイ側は表示位置と出し入れだけを持つ。
  オーバーレイと kyotai/ の相対位置 (`../kyotai/`) を変えないこと。
- 動画素材: `afterblackout/` と `cutin/` は GIF/画像と同じ扱いで mp4/webm/mov を置ける (`assetRecord` の `kind` で分岐)。
  固定素材は `freeze/frz.webm|mp4`・`freeze/moe.webm|mp4` があれば GIF より優先 (`probeVideoVariant`)。
  `<video>` の再生は必ず `startVideo` / `stopVideo` を通す (src 変更直後の play() は Chrome で失敗することがあるため
  `loadedmetadata` を待ってから再生している)。静的配信は Range (206) / Last-Modified (304) 対応済みなので、動画の
  巻き戻し・シークはサーバー側で完結する。
- `main_control.html` と `kyotai.html` は相対パス依存なし。接続先は画面内の ws URL 入力欄 (既定 ws://localhost:8787)。
- 主制御 → 副制御は 2 バイトコマンド (単方向)。副制御は主制御の内部状態を直接見ない。この境界を守る
  (仕様は doc/主制御・副制御仕様書.docx)。
- 中継サーバーはメッセージを「受信したら他の全クライアントへ転送するだけ」。ロジックを足さない。
- 素材 (gif/mp3/wav/jpg) は大きい。バイナリを差し替えるコミットは分ける。

## 起動の前提 (人手)

- setup.bat をダブルクリックすれば依存インストールから全起動まで行う。dev.ps1 と同時に使うとポート競合の警告が出るが問題ない
  (dev.ps1 の stop はポートからも探して止める)。
- Node.js と Python 3 (`py -3` または `python`) が PATH にあること。ws は `npm install` で入る。
- `.claude/settings.json` (Claude Code の許可コマンド) と `.vscode/tasks.json` は `scripts/config/` から
  `scripts\migrate_layout.bat` が配置する。変更するときは `scripts/config/` 側も同じ内容にしておく。
