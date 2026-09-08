# slot_enshutsu_sender

Twitch 配信用のパチスロ演出オーバーレイ。GOD タイプ機の主制御・副制御を Python で再現し、
WebSocket 中継サーバーを介して OBS のオーバーレイ、コンパネ、筐体ビューを連動させます。

## 起動

**ワンクリック (推奨)**: `setup.bat` をダブルクリック。Node.js / Python の確認、`ws` のインストール、
中継サーバー起動、ブラウザウィンドウ表示、主制御の単体テストと起動までを順に行います。

**コマンドライン (Claude Code / VS Code ターミナル)**:

```
scripts\dev.cmd start              # 中継サーバー + 主制御 (実機ウェイト)
scripts\dev.cmd start -Mode fast   # 0.5秒/G の動作確認モード
scripts\dev.cmd start -Mode manual # 勝手に回さず、レバーON待ちで起動 (1ゲームずつ手動)
scripts\dev.cmd status
scripts\dev.cmd test
scripts\dev.cmd stop
```

**手動で1ゲームずつ回す**: `manual.bat` をダブルクリック。主制御を手動モードで起動し、メニューの数字キーで
レバーON / クレジット投入 +50 / 自動・手動の切替 / 状態表示 / 停止ができます。

`-Mode manual` (setup.bat なら選択肢 `[4] 手動`) は、主制御が自分から回さずレバーON入力を待ちます。
コンパネ「主制御 詳細」の 🕹️レバーON、または筐体ビュー (`?mode=link`) で Space / Enter を押すと1ゲーム進みます。
ベットは MAX ベット (3枚) のみで、クレジットが足りないと回りません。クレジットはコンパネの 🪙+3 / 🪙+50
(クレジット投入信号) で足せます (上限なし)。

## URL

| 画面 | URL |
| --- | --- |
| コンパネ | http://localhost:8787/control/main_control.html |
| オーバーレイ (OBS ブラウザソース) | http://localhost:8787/enshutsu/enshutsu_overlay.html |
| 筐体ビュー | http://localhost:8787/reel/reel.html?mode=link&hidebar=1 |
| 図柄設定 (図柄画像の差し替え) | http://localhost:8787/reel/symbol_editor.html |

## フォルダ

| パス | 内容 |
| --- | --- |
| `server/` | WebSocket 中継 + 静的配信サーバー (port 8787) |
| `main_board/` | 主制御・副制御シミュレータ `god_main_board.py` (port 8765) |
| `control/` | コンパネ `main_control.html` |
| `reel/` | 筐体ビュー `reel.html` |
| `enshutsu/` | オーバーレイ本体と演出素材 (`at/sound/`, `yokoku/banner/sound/` に効果音を置くと自動で鳴る。フリーズ素材・萌えカットインは GIF/画像のほか mp4/webm 動画も可) |
| `doc/` | 仕様書 |
| `scripts/` | CLI 用ツール |

開発時の詳細は `CLAUDE.md` を参照してください。
