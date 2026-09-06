# slot_enshutsu_sender

Twitch 配信用のパチスロ演出オーバーレイ。GOD タイプ機の主制御・副制御を Python で再現し、
WebSocket 中継サーバーを介して OBS のオーバーレイ、コンパネ、筐体ビューを連動させます。

## 起動

**ワンクリック (推奨)**: `setup.bat` をダブルクリック。Node.js / Python の確認、`ws` のインストール、
中継サーバー起動、ブラウザウィンドウ表示、主制御の単体テストと起動までを順に行います。

**コマンドライン (Claude Code / VS Code ターミナル)**:

```
scripts\dev.cmd start            # 中継サーバー + 主制御 (実機ウェイト)
scripts\dev.cmd start -Mode fast # 0.5秒/G の動作確認モード
scripts\dev.cmd status
scripts\dev.cmd test
scripts\dev.cmd stop
```

## URL

| 画面 | URL |
| --- | --- |
| コンパネ | http://localhost:8787/control/main_control.html |
| オーバーレイ (OBS ブラウザソース) | http://localhost:8787/enshutsu/enshutsu_overlay.html |
| 筐体ビュー | http://localhost:8787/reel/reel.html?mode=link&hidebar=1 |

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
