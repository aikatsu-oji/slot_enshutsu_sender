予告オーサリングのデータ置き場。

  <id>.json   … シーン1件 (予告1つ分)。予告オーサリング
                 http://localhost:8787/enshutsu/authoring_editor.html で作って保存します
  assets/     … シーンから参照する素材 (画像 / 動画 / 音声)。エディタへドロップすると
                 中継サーバーの POST /api/authoring/asset がここへ保存します

手で JSON を書いても構いません (形は enshutsu/authoring_player.js の先頭のコメント)。
オーバーレイは起動時に GET /api/authoring で全シーンを読み、保存のたびに流れてくる
authoringUpdated を受けて読み直します。

シーンに「割り当て (bind)」を書いておくと、副制御からそのイベントが届いたときに
既定の演出 (予告バナーなど) の代わりに自動で再生されます。
  { "event": "banner", "rank": "赤" }              … 赤ランクの予告バナーの代わり
  { "event": "banner", "rank": "金", "trigger": "stop3" }  … 第3停止の金だけ
  { "event": "gg_start" } / { "event": "at_end" } / { "event": "navi" } なども同じ
割り当てを無視して既定の演出に戻したいときは、オーバーレイの設定パネル
「予告」→「オーサリング」のチェックを外します。

同梱の sample_akatsu.json / sample_chance.json は割り当て無し (手動再生のみ) です。
