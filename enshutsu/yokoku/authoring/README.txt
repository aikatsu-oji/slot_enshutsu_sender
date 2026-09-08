予告オーサリングのデータ置き場。

  <id>.json   … シーン1件 (予告1つ分)。予告オーサリング
                 http://localhost:8787/enshutsu/authoring_editor.html で作って保存します
  assets/     … シーンから参照する素材 (画像 / 動画 / 音声)。エディタへドロップすると
                 中継サーバーの POST /api/authoring/asset がここへ保存します

手で JSON を書いても構いません (形は enshutsu/authoring_player.js の先頭のコメント)。
オーバーレイは起動時に GET /api/authoring で全シーンを読み、保存のたびに流れてくる
authoringUpdated を受けて読み直します。

シーンに「割り当て (bind)」を書いておくと、副制御からそのイベントが届いたときに
自動で再生されます (割り当てが無いイベントは何も出ません)。
  { "event": "banner" }                      … 予告バナー系 (レバーON・各停止) すべて
  { "event": "banner", "trigger": "stop3" }  … 第3停止のときだけ
  { "event": "freeze" } / { "event": "gg_start" } / { "event": "at_end" } / { "event": "navi" } なども同じ
割り当てを無視したいときは、オーバーレイの設定パネル「オーサリング」のチェックを外します。

画像の差し替え:
  クリップに「差し替え名 (slot)」を付けておくと、副制御が
    { "type":"banner", "images": { "<差し替え名>": "<素材のファイル名>" } }
    { "type":"banner", "image": "<素材のファイル名>" }   ← 差し替え名 "main" への指定と同じ
  を送ってきたときに、そのクリップの素材だけを入れ替えて再生します。
  指示が無ければクリップに設定した既定の素材で再生します (デフォルト再生)。

別の予告の呼び出し:
  種類 "scene" のクリップを置くと、その時刻から別のシーンを再生します (src に呼ぶシーンの id)。
  差し替えの指示は呼び出し先にも引き継ぎます。呼び出しの深さは 4 段までです。

同梱の sample_akatsu.json / sample_chance.json は割り当て無し (手動再生のみ) です。
