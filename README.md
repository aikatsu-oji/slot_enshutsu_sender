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

**Twitch 連携 (段階1: 演出のみ)**:

```
npm run twitch:mock              # Twitch に繋がず擬似イベントで演出とメダル投入を確認
npm run twitch                   # 本番 (初回は .run/twitch_config.json の用意と認証が必要)
node twitch/twitch_bridge.js --chat <channel>   # コメント連動だけなら認証不要 (チャンネル名だけ)
scripts\dev.cmd start -Credit    # 主制御をクレジット制で起動 (メダルが尽きたら待機)
```

認証の手順は **`doc/twitch認証の取り方.md`**。コメント連動だけなら認証は不要です (`--chat`)。

`twitch/config.example.json` を `.run/twitch_config.json` にコピーして `clientId` と `channel` を入れ、
中継サーバーとオーバーレイを開いた状態で起動します。状態確認・キルスイッチ・疑似イベントは
コンパネの「Twitch 連携」カードから。詳細は `doc/twitch連携設計.md`。

中継サーバーは既定で 127.0.0.1 だけを待ち受けます。別 PC から繋ぐときだけ `HOST=0.0.0.0` を付けてください。

## URL

| 画面 | URL |
| --- | --- |
| コンパネ (Twitch 連携カードもここ) | http://localhost:8787/control/main_control.html |
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
| `twitch/` | Twitch 連携ブリッジ (`twitch_bridge.js`)。段階1 は演出のみで、主制御には触らない |
| `doc/` | 仕様書 (`twitch連携設計.md` に Twitch 連携の設計) |
| `scripts/` | CLI 用ツール |

開発時の詳細は `CLAUDE.md` を参照してください。
