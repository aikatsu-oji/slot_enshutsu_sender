#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GODタイプ パチスロ機 主制御（メイン基板）シミュレータ

実機の主制御が1ゲームで実行する処理シーケンスを、そのまま関数単位に分解して再現する。

    [1] クレジット投入 / MAXベット -> insert_credit() / bet()
    [2] 乱数取得（16bit）      -> get_random()
    [3] 内部抽選（役決定）      -> lottery()
    [4] 当否抽選（AT/ストック） -> hit_lottery()
    [5] 演出抽選（予告/フリーズ）-> enshutsu_lottery()
    [6] リール回転開始         -> spin_start()
    [7] 停止制御（引込み/蹴り） -> stop_reel()
    [8] 入賞判定（有効ライン）  -> judge()
    [9] 払出                  -> payout()
   [10] 状態遷移（AT/RT管理）   -> update_state()

本機はメイン管理AT（6号機準拠）として実装する。すなわちAT状態・ゲーム数・
ストックはすべて主制御が保持し、副制御（演出基板）には結果を通知するだけとする。

演出も同じ扱いにする。当該ゲームの当否はレバーONの時点で [4] が確定させ、
[5] がその結果と内部モードから予告パターンを1つ抽選して、番号だけを副制御へ渡す。
副制御は受け取った番号を絵に起こすだけで、自前の抽選を持たない。

ベットはMAXベット（規定投入枚数3枚）のみで、部分ベットは持たない。回転はMAXベットが
成立したときだけ。クレジットはクレジット投入信号で増え、上限は設けない（無限）。

実在の遊技機の仕様値ではなく、GODタイプに共通する構造を模したオリジナル諸元。

出力ポートは2系統:
    副制御ポート   … 2バイトコマンド（単方向）。--serve で ws://127.0.0.1:8765 から
                    演出イベントとしてオーバーレイへ配信される。
    試験用モニタ端子 … 主制御の全レジスタ。trigger_relay_server.js（ws://127.0.0.1:8787）
                    へクライアント接続し、コンパネ main_control.html へ流す（第9節）。

usage:
    python3 god_main_board.py --setting 1 --games 10000   # 集計のみ（コンパネ送信なし）
    python3 god_main_board.py --ladder                    # 設定1〜6の機械割を並べて逆転を確認
    python3 god_main_board.py --trace 30                  # 1G毎ログ＋コンパネ送信
    python3 god_main_board.py --serve --games 1000        # 実機ウェイトで稼働。副→オーバーレイ、主→コンパネ
    python3 god_main_board.py --serve --manual --credit 50  # 回さずレバーON待ち。クレジット50枚を入れて起動
    python3 god_main_board.py --serve --panel-cmds        # 2バイトコマンド生ログもコンパネへ
    python3 god_main_board.py --serve --no-panel          # コンパネ送信を切る
    python3 god_main_board.py --trace 30 --manual         # 端末でEnterを押すたびに1ゲームだけ進める
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import queue
import random
import socket
import sys
import threading
import time
from dataclasses import dataclass, field
from urllib.parse import urlparse

# ---------------------------------------------------------------------------
# 1. 図柄配列（リールテープ）  各リール21コマ
#    定義は同じフォルダの reels.json（筐体ビュー reel/reel.html も同じファイルを読む）
# ---------------------------------------------------------------------------

with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "reels.json"), encoding="utf-8") as _f:
    _REEL_DATA = json.load(_f)

REEL_L, REEL_C, REEL_R = (list(t) for t in _REEL_DATA["reels"])

REELS = (REEL_L, REEL_C, REEL_R)
KOMA = int(_REEL_DATA["koma"])   # 1リールのコマ数
assert all(len(t) == KOMA for t in REELS), "reels.json: コマ数が一致しません"
MAX_SLIP = 4       # 最大滑りコマ数（法定：190ms以内 = 4コマ）
BET = 3            # 規定投入枚数

# ---------------------------------------------------------------------------
# 2. 役（条件装置）と払出枚数
# ---------------------------------------------------------------------------

PAYOUT = {
    "神揃い": 0,        # 払出なし。AT直撃契機のみ
    "赤7揃い": 0,
    "リプレイ": 3,      # 再遊技（投入分を返す扱い）
    "共通ベル": 8,
    "押順ベル": 8,      # 正解時のみ。不正解は「こぼし」1枚
    "こぼし": 1,
    "スイカ": 3,
    "チャンス目": 0,
    "ハズレ": 0,
}

# 内部抽選テーブル（分母65536・設定1〜6）
# 押順ベルは6択（正解は1/6）。数値は主制御の抽選値そのものを模す。
#
# 設定差を持つ役はすべて設定1→6で単調増加させ、隣り合う設定の差も
# 「上の設定ほど大きい」順に並べる（設定1と2だけ差が小さい・設定6だけ跳ねる、
# といった歪みを作らない）。神揃いは設定4以上でのみ優遇する（456有利）。
LOTTERY_TABLE = {
    #  役          設定1   設定2   設定3   設定4   設定5   設定6
    "神揃い":     (    8,      8,      8,      9,     10,     11),
    "押順ベル":   (40000,  40000,  40000,  40000,  40000,  40000),
    "共通ベル":   ( 1900,   1950,   2010,   2080,   2160,   2250),   # 旧260〜320。通常時の出玉調整はここ
    "リプレイ":   ( 6000,   6000,   6000,   6000,   6000,   6000),
    "スイカ":     (  650,    665,    685,    710,    740,    775),
    "チャンス目": (  360,    372,    388,    408,    432,    460),
}

# AT中の抽選テーブル（通常時と異なる役だけ上書き）。共通ベルを設定差で引き上げる。
# 機械割の主調整はここで行う。値は 1900 + 1100×(設定-1) の等差にしてあり、
# AT中1Gあたりの純増が設定を1つ上げるごとに一定歩調で伸びる。
LOTTERY_TABLE_AT = dict(LOTTERY_TABLE, **{
    #  役          設定1   設定2   設定3   設定4   設定5   設定6
    "共通ベル":   ( 1900,   3000,   4100,   5200,   6300,   7400),
})

# 上記テーブルでの機械割（設定ごとに8000万G測定・--ladder で再現できる）
#   設定1  97.7%   設定2  99.8%   設定3 102.2%
#   設定4 105.6%   設定5 109.4%   設定6 114.0%
# 隣接設定の差は +2.2 / +2.4 / +3.4 / +3.9 / +4.6 ポイントで、
# 「設定を1つ上げるほど伸びが大きい」順に並ぶ（どこにも逆転が無い）。
# テーブルを触ったら --ladder で6設定を測り直し、逆転が出ていないか確認すること
# （1万G程度の単発集計は±10%以上ぶれるので、設定差の判断には使えない）。

# ---------------------------------------------------------------------------
# 3. 状態定義
# ---------------------------------------------------------------------------

ST_NORMAL, ST_GG, ST_AT = 0, 1, 2          # 遊技状態
MODE_LOW, MODE_HIGH, MODE_SHIGH = 0, 1, 2  # 通常時の内部モード（低確/高確/超高確）

CEILING = 1200        # 天井ゲーム数
CEILING_ZONE = 32     # 天井までの残りゲーム数がこれ以下なら前兆扱い（演出抽選だけが見る）
GG_GAMES = 10         # ゴッドゲームの固定ゲーム数
AT_INIT_GAMES = 50    # ストック1個あたりの初期ATゲーム数
AT_ADD_GAMES = 30     # AT中チャンス目からの上乗せゲーム数
AT_ADD_RATE = 0.35    # AT中チャンス目の上乗せ当選率
AT_STOCK_RATE = 0.20  # AT中スイカのストック当選率

# レア役からのGG（ゴッドゲーム）当選率  [低確, 高確, 超高確]
GG_RATE = {
    "スイカ":     (0.045, 0.130, 0.360),
    "チャンス目": (0.160, 0.360, 0.750),
}

# モード昇格率（レア役成立時）  -> (高確, 超高確)
MODE_UP = {
    "スイカ":     (0.250, 0.020),
    "チャンス目": (0.450, 0.100),
}
MODE_GAMES = 32       # 高確以上の滞在ゲーム数

GG_SEVEN_RATE = 0.185  # GG中1Gあたりの赤7揃い（ストック+1）
GG_GOD_RATE = 0.005    # GG中1Gあたりの神揃い（ストック+5）
GOD_STOCK = 5         # 神揃い時の獲得ストック

# ウェイト。規則で遊技間隔は4.1秒以上（前回の「回転開始」から計る）。
# 主制御はこの間レバーONを受け付けない＝レバーON無効時間。
WAIT_TIME = 4.1


# ---------------------------------------------------------------------------
# 4. 主制御 → 副制御 コマンド（単方向シリアル・2バイト）
#
#    上位バイト = コマンド種別 / 下位バイト = データ。
#    通信は主→副の一方向のみ。副制御は主制御へ一切送信できず、
#    主制御は副制御の状態を参照しない（副が落ちても遊技は続行する）。
#    内部モード（低確/高確/超高確）そのものは送信しない。代わりに主制御が
#    モードと当否を材料に演出を抽選し、その結果である演出パターン番号
#    （0x22 / 0x23）だけを渡す。副制御は番号を絵に起こすだけで、
#    主制御の内部状態は最後まで見えない。
# ---------------------------------------------------------------------------

CMD_POWER_ON = 0x01     # 電源投入・復帰
CMD_MEDAL_IN = 0x10     # メダル投入   data: 投入枚数
CMD_GAME_START = 0x11   # 遊技開始     data: 遊技状態
CMD_FLAG = 0x20         # 内部当選     data: 条件装置番号
CMD_NAVI = 0x21         # 押し順ナビ   data: 押し順番号(0-5) / 0xFF=ナビなし
CMD_ENSHUTSU = 0x22     # 演出パターン data: 予告パターン番号(0=演出なし)。毎ゲーム送る
CMD_FREEZE = 0x23       # フリーズ     data: ロック段階(1〜3)。抽選で当たったゲームだけ送る
CMD_REEL_START = 0x30   # 全リール回転開始
CMD_REEL_STOP_L = 0x31  # 左リール停止 data: 停止位置(コマ番号)
CMD_REEL_STOP_C = 0x32
CMD_REEL_STOP_R = 0x33
CMD_ALL_STOP = 0x34     # 全停止       data: 表示役番号
CMD_PAYOUT = 0x40       # 払出         data: 払出枚数
CMD_GAME_END = 0x41     # 遊技終了     data: 通常時ゲーム数(下位8bit)
CMD_STATE_NOTIFY = 0x42 # 状態通知     data: 遊技状態。遊技終了後に毎ゲーム送る（移行の有無によらない）
CMD_STATE = 0x50        # 状態移行     data: 遊技状態
CMD_STOCK = 0x51        # ストック数   data: 個数
CMD_AT_GAMES = 0x52     # AT残ゲーム数 data: 残G(下位8bit)
CMD_ADD_GAMES = 0x53    # 上乗せ       data: 上乗せG数

CMD_NAME = {
    CMD_POWER_ON: "電源投入", CMD_MEDAL_IN: "メダル投入", CMD_GAME_START: "遊技開始",
    CMD_FLAG: "内部当選", CMD_NAVI: "押し順ナビ",
    CMD_ENSHUTSU: "演出パターン", CMD_FREEZE: "フリーズ", CMD_REEL_START: "リール回転",
    CMD_REEL_STOP_L: "左リール停止", CMD_REEL_STOP_C: "中リール停止",
    CMD_REEL_STOP_R: "右リール停止", CMD_ALL_STOP: "全停止", CMD_PAYOUT: "払出",
    CMD_GAME_END: "遊技終了", CMD_STATE_NOTIFY: "状態通知",
    CMD_STATE: "状態移行", CMD_STOCK: "ストック数",
    CMD_AT_GAMES: "AT残G", CMD_ADD_GAMES: "上乗せ",
}

# 条件装置番号（主副で共有する定数）
FLAG_ID = {name: i for i, name in enumerate(
    ["ハズレ", "リプレイ", "共通ベル", "押順ベル", "こぼし", "スイカ",
     "チャンス目", "赤7揃い", "神揃い"])}
ID_FLAG = {v: k for k, v in FLAG_ID.items()}


# ---------------------------------------------------------------------------
# 5. 演出抽選テーブル（主副で共有する定数）
#
#    演出を決めるのは主制御。内部当選役・内部モード・天井までの残りゲーム数・
#    当該ゲームの当否といった、主制御しか持たない情報から予告パターンを1つ抽選し、
#    その番号だけを 0x22（演出パターン）/ 0x23（フリーズ）で副制御へ渡す。
#    副制御はここの表を同じ番号で引いて絵に起こすだけで、自前の抽選を持たない。
#
#    演出用の乱数は出玉抽選（MainBoard.rng）とは別系統（MainBoard.enshutsu_rng）に
#    してある。ここの重みをいくら触っても機械割は動かない。
# ---------------------------------------------------------------------------

ENSHUTSU_RANK = ["白", "青", "緑", "赤", "金"]     # 予告ランク（弱→強）

# 演出抽選での役グループ。内部当選役をここで3段階に丸める（記載の無い役は「弱」）。
ENSHUTSU_GROUP = {
    "スイカ": "中",
    "チャンス目": "強",
}

# 最終ランクの抽選テーブル。
#   行 = 内部モード（低確 / 高確 / 超高確）。天井前兆は1段上の行を使う。
#   列 = [演出なし, 白, 青, 緑, 赤, 金] の重み（合計は行ごとに任意）。
# 上の行ほど強いランクが出るので、副制御が高確度を推測しなくても期待度が絵に出る。
ENSHUTSU_TABLE = {
    #  役グループ    演出なし    白    青    緑    赤    金
    "弱": ((9600,  300,   80,   20,    0,    0),
           (9450,  350,  150,   50,    0,    0),
           (9100,  500,  280,  100,   20,    0)),
    "中": ((4000, 3000, 2000,  800,  180,   20),
           (2500, 2600, 3000, 1500,  360,   40),
           (1200, 1800, 3200, 3000,  720,   80)),
    "強": ((2000, 2500, 3000, 2000,  450,   50),
           (1000, 2000, 3000, 3000,  900,  100),
           ( 400, 1200, 2600, 4000, 1600,  200)),
}

# GG当選ゲーム専用の表。演出なしを引かせず、赤・金へ寄せる（当選の重さを出す）。
# 主制御が当否をレバーONで確定させているからこそ引ける表で、
# 「当たっているゲームだけ強い」という当たり前の期待度をここで作る。
ENSHUTSU_TABLE_HIT = ((0, 400, 1200, 3000, 4000, 1400),
                      (0, 200,  800, 2500, 4500, 2000),
                      (0, 100,  400, 2000, 4800, 2700))

# 最終ランクごとの「最後に出す操作時点」の重み [レバーON, 第1停止, 第2停止, 第3停止]。
# 高ランクほど遅い時点まで引っ張り、ステップアップさせやすい。
ENSHUTSU_STEP = {
    "白": (70, 15, 10,  5),
    "青": (45, 20, 20, 15),
    "緑": (25, 20, 25, 30),
    "赤": (15, 15, 25, 45),
    "金": (10, 10, 20, 60),
}
ENSHUTSU_STAGE = (50, 30, 15, 5)   # 段階数 1,2,3,4 の重み（可能な範囲で切り詰める）

ENSHUTSU_POINT = ("レバー", "第1", "第2", "第3")   # 予告を出す4つの操作時点


def _build_enshutsu_patterns():
    """予告パターンの全表を作る（主副が同じ番号で引く対応表）。

    1パターン = [レバーON, 第1停止, 第2停止, 第3停止] の各時点に出すランク番号
    （None はその時点では何も出さない）。最終ランク t・最後に出す時点 f・
    段階数 k の組をすべて並べたもので、番号0は「演出なし」。
    例: t=赤(3) f=第3停止(3) k=3 → (None, 青, 緑, 赤)。
    番号は 0x22 の1バイトに収める（現在41通り）。
    """
    patterns = [(None, None, None, None)]
    ids = {}
    for t in range(len(ENSHUTSU_RANK)):
        for f in range(4):
            for k in range(1, min(f, t) + 2):
                p = [None] * 4
                for j in range(k):
                    p[f - (k - 1) + j] = t - (k - 1) + j
                ids[(t, f, k)] = len(patterns)
                patterns.append(tuple(p))
    return tuple(patterns), ids


ENSHUTSU_PATTERN, ENSHUTSU_PATTERN_ID = _build_enshutsu_patterns()
assert len(ENSHUTSU_PATTERN) <= 256, "演出パターン番号は0x22の1バイトに収めること"


def enshutsu_note(pid: int) -> str:
    """演出パターン番号を読める形にする（コマンド生ログ・コンパネ表示用）。"""
    pat = ENSHUTSU_PATTERN[pid] if 0 <= pid < len(ENSHUTSU_PATTERN) else None
    if pat is None:
        return f"未定義({pid})"
    steps = [f"{ENSHUTSU_POINT[i]}:{ENSHUTSU_RANK[v]}"
             for i, v in enumerate(pat) if v is not None]
    return "→".join(steps) if steps else "演出なし"


# フリーズ（レバーONロック）抽選。出玉に直結する演出なので主制御が持つ。
#   [ロックなし, ロック1(振動), ロック2(カットイン), ロック3(暗転)] の重み。
# 神揃い（通常時の内部当選・GG中の神揃いのどちらも）は必ずフル（ロック3）。
# それ以外のGG当選でまれに出る短いロックは「出たら当選確定」の演出で、ガセは引かせない。
# 当否をレバーONで確定させている（hit_lottery）から引ける表で、
# 副制御が受信情報だけから推測していた頃には作れなかった演出になる。
FREEZE_TABLE = {
    "神揃い":  (   0,   0,  0, 100),
    "GG当選":  (9700, 220, 80,   0),
}
FREEZE_SEQ = ("lock1", "lock2", "lock3")


# ---------------------------------------------------------------------------
# 6. 主制御の内部レジスタ
# ---------------------------------------------------------------------------

@dataclass
class MainBoard:
    setting: int = 1
    rng: random.Random = field(default_factory=random.Random)

    # 遊技状態
    state: int = ST_NORMAL
    mode: int = MODE_LOW
    mode_left: int = 0
    game_count: int = 0        # 通常時ゲーム数（天井カウンタ）
    gg_left: int = 0
    at_left: int = 0
    stock: int = 0

    # クレジット（貯留）。ベットはMAXベット（規定投入枚数3枚）のみで、
    # 3クレジット無いと回転できない。最大クレジットは無限（上限なし）。
    credit: int = 0
    # クレジットが足りないとき、その場で投入したことにするか。
    # 集計・検証モード（--games / --ladder / --sim / --commands / --events / --trace）は
    # メダルが無限にある台として扱うので既定は True。
    # 稼働モード（--serve）は自動・手動のどちらでも False にし、実機と同じく
    # クレジット投入信号を必須とする（貯留が尽きたら回らない）。
    auto_insert: bool = True

    # 出玉カウンタ
    total_in: int = 0
    total_out: int = 0
    total_games: int = 0

    # 当該ゲームのワーク
    flag: str = "ハズレ"
    bell_answer: int = 0       # 押順ベルの正解押し順（0-5）
    reel_pos: list = field(default_factory=lambda: [0, 0, 0])
    prize: str = "ハズレ"
    notice: list = field(default_factory=list)
    # レバーONで確定した当該ゲームの当否（hit_lottery が入れ、update_state が消費する）。
    # 演出抽選はこれを見て「当たっているゲームだけ強い予告」を出す。
    hit: dict = field(default_factory=dict)

    # 演出抽選。演出用の乱数は出玉抽選（rng）とは別系統にして、演出テーブルを
    # いくら触っても機械割が動かないようにする。
    enshutsu_rng: random.Random = field(default_factory=random.Random)
    draw_enshutsu: bool = True  # 演出を抽選するか。大量集計（--ladder / --sim）では切る
    enshutsu: int = 0           # 当該ゲームの予告パターン番号（0x22 で送る値）
    freeze: int = 0             # 当該ゲームのフリーズ段階（0x23 で送る値。0=ロックなし）

    # 副制御ポート（単方向送信専用）
    sub: "SubBoard | None" = None
    cmd_log: list = field(default_factory=list)
    log_cmds: bool = True      # cmd_log に残すか。長時間の集計（--ladder）では切る

    # 試験用モニタ端子（コンパネ main_control.html 向け）。副制御とは別ポートで、
    # 内部モードを含む全レジスタを出す。遊技には一切影響しない（送信失敗は無視）。
    panel: "PanelLink | None" = None

    # ウェイト（レバーON無効時間）。実時間で動かす --serve のときだけ使う。
    # 集計モード（--games / --ladder）は 0 のままにして時計を一切見ない
    # （5,000,000G×6設定を回すので、1ゲームあたりの time.monotonic() でも効く）。
    wait_time: float = 0.0     # 回転開始から次のレバーONを受け付けるまでの秒数
    spin_at: float = 0.0       # 直近の回転開始時刻（time.monotonic）
    hold_until: float = 0.0    # フリーズ等で回転開始をさらに止めている期限

    # -- [0] 主制御 → 副制御 送信 ------------------------------------------
    def send(self, cmd_type: int, data: int = 0) -> None:
        """2バイトコマンドを副制御へ送出する。戻り値は受け取らない（単方向）。"""
        cmd = ((cmd_type & 0xFF) << 8) | (data & 0xFF)
        if self.log_cmds:
            self.cmd_log.append(cmd)
        if self.sub is not None:
            self.sub.recv(cmd)
        if self.panel is not None:
            self.panel.tap_cmd(cmd)

    def power_on(self) -> None:
        self.send(CMD_POWER_ON, self.state)

    # -- [0.1] ラムクリア ----------------------------------------------------
    def ram_clear(self) -> None:
        """ラムクリア。実機の設定変更/RAMクリアに相当し、遊技に関わるRAMを全て消す。

        消えるもの：遊技状態・内部モード・天井カウンタ・GG/AT残ゲーム数・ストック・
        クレジット（貯留）・出玉カウンタ・当該ゲームのワーク（当否と演出抽選結果を含む）。
        設定は据え置きにする（実機でもラムクリア単独では設定は変わらない）。
        最後に電源投入コマンド(0x01)を送り直すので、副制御も自分の写しを初期化する。
        """
        self.state = ST_NORMAL
        self.mode, self.mode_left = MODE_LOW, 0
        self.game_count = 0
        self.gg_left = 0
        self.at_left = 0
        self.stock = 0
        self.credit = 0
        self.total_in = self.total_out = self.total_games = 0
        self.flag = "ハズレ"
        self.prize = "ハズレ"
        self.bell_answer = 0
        self.reel_pos = [0, 0, 0]
        self.notice = []
        self.hit = {}
        self.enshutsu = 0
        self.freeze = 0
        self.cmd_log.clear()
        # ウェイトも電源投入直後と同じ扱いに戻す（直前の回転が無かったことにする）
        self.spin_at = 0.0
        self.hold_until = 0.0
        self.power_on()

    # -- [0.5] 入力受付状態（ウェイト） --------------------------------------
    #    規則上のウェイトは「前回の回転開始」からの経過で決まる。遊技そのものに
    #    かかった時間（フリーズを含む）がウェイトを食うので、加算ではなく期限で持つ。
    def accept_at(self) -> float:
        """次にレバーONを受け付ける時刻（time.monotonic 基準）。"""
        return max(self.spin_at + self.wait_time, self.hold_until)

    def wait_left(self, now: float | None = None) -> float:
        """レバーON無効時間の残り秒数。0 なら受付可。"""
        if self.wait_time <= 0.0 and self.hold_until <= 0.0:
            return 0.0
        return max(0.0, self.accept_at() - (time.monotonic() if now is None else now))

    def accepts_lever(self, now: float | None = None) -> bool:
        return self.wait_left(now) <= 0.0

    def hold_lever(self, sec: float) -> None:
        """フリーズなど、回転開始（＝レバーON受付）をさらに sec 秒止める。"""
        if sec > 0.0:
            self.hold_until = max(self.hold_until, time.monotonic() + sec)

    # -- [1] クレジット投入 / MAXベット --------------------------------------
    def insert_credit(self, n: int = 1) -> int:
        """クレジット投入信号。入った枚数だけ貯留に足す（最大クレジットは無限）。"""
        n = max(0, int(n))
        self.credit += n
        return self.credit

    def can_bet(self) -> bool:
        """MAXベットできるか。規定投入枚数に足りなければ回転できない。"""
        return self.credit >= BET or self.auto_insert

    def bet(self) -> bool:
        """MAXベット（規定投入枚数3枚）。ベットはこれ1種類だけで、部分ベットは無い。

        クレジットが3枚に満たなければベットせず False を返す（＝回転できない）。
        auto_insert のときだけ、不足ぶんをその場で投入したことにして続行する。
        """
        if self.credit < BET:
            if not self.auto_insert:
                return False
            self.insert_credit(BET - self.credit)
        self.credit -= BET
        self.total_in += BET
        self.total_games += 1
        self.send(CMD_MEDAL_IN, BET)
        self.send(CMD_GAME_START, self.state)
        return True

    # -- [2] 乱数取得 ------------------------------------------------------
    def get_random(self) -> int:
        """16bitハードウェア乱数（0-65535）を1つラッチする。"""
        return self.rng.randrange(65536)

    # -- [3] 内部抽選 ------------------------------------------------------
    def lottery(self) -> str:
        r = self.get_random()
        acc = 0
        idx = self.setting - 1
        table = LOTTERY_TABLE_AT if self.state == ST_AT else LOTTERY_TABLE
        for name, values in table.items():
            acc += values[idx]
            if r < acc:
                self.flag = name
                break
        else:
            self.flag = "ハズレ"

        if self.flag == "押順ベル":
            self.bell_answer = self.get_random() % 6
        self.send(CMD_FLAG, FLAG_ID[self.flag])
        return self.flag

    # -- [4] 当否抽選 ------------------------------------------------------
    def hit_lottery(self) -> dict:
        """当該ゲームの当否をレバーONの時点で確定させる。

        実機の主制御はレバーONの1回で役もAT当選も決めてしまい、リールが回り
        始めた時点で結果は動かない。ここで確定させておくと、次段の演出抽選が
        「このゲームは当たっている」を知ったうえで予告を選べる。
        引いた結果は self.hit に置くだけで、状態は変えない（反映は update_state）。

        戻り値（すべて任意キー）:
            gg       … レア役からのGG当選。値は当選契機の役名
            ceiling  … 天井到達でのGG当選
            mode_up  … 通常時の内部モード昇格先（None は昇格しない）
            god      … 神揃い（通常時は内部当選、GG中はGG中の神揃い抽選）
            seven    … GG中の赤7揃い（ストック+1）
            add      … AT中の上乗せゲーム数
            stock    … AT中のストック獲得数
        """
        flag = self.flag
        h: dict = {}
        if flag == "神揃い":
            # 神揃いは内部当選そのものが当選。状態を問わず最優先で扱う
            h["god"] = True
        elif self.state == ST_NORMAL:
            if flag in GG_RATE and self.rng.random() < GG_RATE[flag][self.mode]:
                h["gg"] = flag
            else:
                if flag in MODE_UP:
                    hi, shi = MODE_UP[flag]
                    r = self.rng.random()
                    h["mode_up"] = (MODE_SHIGH if r < shi else
                                    MODE_HIGH if r < shi + hi else None)
                if self.game_count + 1 >= CEILING:
                    h["ceiling"] = True
        elif self.state == ST_GG:
            r = self.rng.random()
            if r < GG_GOD_RATE:
                h["god"] = True
            elif r < GG_GOD_RATE + GG_SEVEN_RATE:
                h["seven"] = True
        else:                                   # ST_AT
            if flag == "チャンス目" and self.rng.random() < AT_ADD_RATE:
                h["add"] = AT_ADD_GAMES
            elif flag == "スイカ" and self.rng.random() < AT_STOCK_RATE:
                h["stock"] = 1
        self.hit = h
        return h

    # -- [5] 演出抽選 ------------------------------------------------------
    def enshutsu_lottery(self) -> int:
        """予告演出とフリーズを抽選し、その番号だけを副制御へ渡す。

        材料は内部当選役・遊技状態・内部モード・天井までの残りゲーム数・当否で、
        どれも主制御しか持たない情報。副制御へ出るのは 0x22（予告パターン番号）と
        0x23（フリーズ段階）だけなので、副制御は内部状態を知らないまま
        主制御が決めた期待度どおりの演出を出せる。

        0x22 は演出が無くても毎ゲーム送り（副制御が今ゲームの分を取り違えない）、
        0x23 はロックを引いたゲームだけ送る。

        draw_enshutsu が False のときは何も抽選せず、コマンドも出さない。
        機械割を測るだけの大量集計（--ladder / --sim）で演出抽選の時間を省くため。
        """
        self.enshutsu = self.freeze = 0
        if not self.draw_enshutsu:
            return 0
        self.freeze = self._draw_freeze()
        if self.freeze:
            # フリーズそのものが今ゲームの演出。予告は重ねない
            self.enshutsu = 0
            self.send(CMD_ENSHUTSU, 0)
            self.send(CMD_FREEZE, self.freeze)
            return 0
        self.enshutsu = self._draw_pattern(self._draw_rank())
        self.send(CMD_ENSHUTSU, self.enshutsu)
        return self.enshutsu

    def _draw_freeze(self) -> int:
        """フリーズ（レバーONロック）の段階。0 はロックなし。

        神揃いは内部当選（通常時）でもGG中の神揃い抽選でも hit["god"] が立つので、
        どちらの神揃いも同じようにフルフリーズになる。
        """
        if self.hit.get("god"):
            cause = "神揃い"
        elif self.hit.get("gg") or self.hit.get("ceiling"):
            cause = "GG当選"
        else:
            return 0
        return self.enshutsu_rng.choices(range(4), weights=FREEZE_TABLE[cause])[0]

    def _draw_rank(self):
        """今ゲームの予告の最終ランク。None は予告なし。"""
        if self.state != ST_NORMAL:
            return None          # AT/GG中は予告バナーを出さない（押し順ナビが主役）
        m = self.mode
        if CEILING - self.game_count <= CEILING_ZONE:
            m = min(m + 1, MODE_SHIGH)      # 天井前兆。1段上のモードとして扱う
        if self.hit.get("gg") or self.hit.get("ceiling"):
            weights = ENSHUTSU_TABLE_HIT[m]
        else:
            weights = ENSHUTSU_TABLE[ENSHUTSU_GROUP.get(self.flag, "弱")][m]
        i = self.enshutsu_rng.choices(range(len(ENSHUTSU_RANK) + 1), weights=weights)[0]
        return None if i == 0 else ENSHUTSU_RANK[i - 1]

    def _draw_pattern(self, rank) -> int:
        """最終ランクから予告パターン番号を1つ選ぶ。

        「最後に出す操作時点 f」と「段階数 k」を抽選し、f を終点に k 段階で
        最終ランクまで上げる（ステップアップ予告）。
        例: 赤・f=第3停止・k=3 → 第1停止:青 → 第2停止:緑 → 第3停止:赤。
        """
        if rank is None:
            return 0
        t = ENSHUTSU_RANK.index(rank)
        f = self.enshutsu_rng.choices(range(4), weights=ENSHUTSU_STEP[rank])[0]
        kmax = min(f, t) + 1
        k = self.enshutsu_rng.choices(range(1, kmax + 1), weights=ENSHUTSU_STAGE[:kmax])[0]
        return ENSHUTSU_PATTERN_ID[(t, f, k)]

    # -- [6] リール回転 ----------------------------------------------------
    def spin_start(self) -> list:
        """各リールの目押し位置（=遊技者の停止操作位置）を決める。"""
        if self.wait_time > 0.0:
            self.spin_at = time.monotonic()   # ウェイトの起点は回転開始
        self.send(CMD_REEL_START)
        return [self.get_random() % KOMA for _ in range(3)]

    # -- [7] 停止制御 ------------------------------------------------------
    def stop_reel(self, reel_idx: int, push_pos: int, target: str | None) -> int:
        """
        主制御の停止制御テーブル相当。
        target図柄が最大4コマ以内で有効ライン（中段）に引き込めれば引き込む。
        引き込めない／targetがNone（=非成立）の場合は、その図柄を蹴って停止する。
        """
        tape = REELS[reel_idx]
        if target:
            for slip in range(MAX_SLIP + 1):
                pos = (push_pos + slip) % KOMA
                if tape[pos] == target:
                    return pos
            # 引き込めない＝取りこぼし
            return (push_pos + MAX_SLIP) % KOMA
        # 非成立図柄の蹴り制御
        for slip in range(MAX_SLIP + 1):
            pos = (push_pos + slip) % KOMA
            if tape[pos] not in ("神", "赤7"):
                return pos
        return push_pos

    # -- [8] 入賞判定 ------------------------------------------------------
    def judge(self, push: list, order: int) -> str:
        """
        有効ライン（中段一直線）の図柄組合せから入賞役を確定する。
        押し順ベルはorder（遊技者の押し順）が正解と一致した場合のみ入賞。
        """
        flag = self.flag
        target = {
            "神揃い": "神", "赤7揃い": "赤7", "リプレイ": "リプ",
            "共通ベル": "ベル", "押順ベル": "ベル",
            "スイカ": "スイカ",
        }.get(flag)

        if flag == "押順ベル" and order != self.bell_answer:
            target = None   # 不正解 -> ベルは引き込まない（こぼし）

        self.reel_pos = [self.stop_reel(i, push[i], target) for i in range(3)]
        for i, cmd in enumerate((CMD_REEL_STOP_L, CMD_REEL_STOP_C, CMD_REEL_STOP_R)):
            self.send(cmd, self.reel_pos[i])
        line = [REELS[i][self.reel_pos[i]] for i in range(3)]

        if target and all(s == target for s in line):
            self.prize = flag
        elif flag == "押順ベル":
            self.prize = "こぼし"
        else:
            self.prize = "ハズレ" if flag in ("チャンス目", "ハズレ") else "ハズレ"
            if flag == "チャンス目":
                self.prize = "チャンス目"
        self.send(CMD_ALL_STOP, FLAG_ID[self.prize])
        return self.prize

    # -- [9] 払出 ----------------------------------------------------------
    def payout(self) -> int:
        p = PAYOUT.get(self.prize, 0)
        self.total_out += p
        self.credit += p        # 最大クレジットは無限なので、払出は全部クレジットへ入る
        self.send(CMD_PAYOUT, p)
        return p

    # -- [10] 状態遷移 ------------------------------------------------------
    def update_state(self) -> None:
        """レバーONで確定済みの当否（self.hit）を遊技状態へ反映する。ここでは抽選しない。"""
        flag = self.flag
        h = self.hit
        self.notice = []

        if flag == "神揃い":
            self.stock += GOD_STOCK
            self.notice.append("神揃い")
            self.send(CMD_STOCK, self.stock)
            self._enter_gg("神揃い")
            return

        if self.state == ST_NORMAL:
            self.game_count += 1
            if h.get("gg"):
                self._enter_gg(h["gg"])
                return
            up = h.get("mode_up")
            if up == MODE_SHIGH:
                self.mode, self.mode_left = MODE_SHIGH, MODE_GAMES
            elif up == MODE_HIGH:
                self.mode = max(self.mode, MODE_HIGH)
                self.mode_left = MODE_GAMES
            if self.mode_left > 0:
                self.mode_left -= 1
                if self.mode_left == 0:
                    self.mode = MODE_LOW
            if h.get("ceiling"):
                self._enter_gg("天井")

        elif self.state == ST_GG:
            if h.get("god"):
                self.stock += GOD_STOCK
                self.notice.append("神揃い")
                self.send(CMD_STOCK, self.stock)
            elif h.get("seven"):
                self.stock += 1
                self.notice.append("赤7揃い")
                self.send(CMD_STOCK, self.stock)
            self.gg_left -= 1
            if self.gg_left <= 0:
                self.state = ST_AT
                self.stock -= 1
                self.at_left = AT_INIT_GAMES
                self.notice.append("AT開始")
                self.send(CMD_STATE, ST_AT)
                self.send(CMD_AT_GAMES, self.at_left)
                self.send(CMD_STOCK, self.stock)

        else:  # ST_AT
            self.at_left -= 1
            if h.get("add"):
                self.at_left += h["add"]
                self.notice.append(f"+{h['add']}G")
                self.send(CMD_ADD_GAMES, h["add"])
            elif h.get("stock"):
                self.stock += h["stock"]
                self.notice.append(f"ストック+{h['stock']}")
                self.send(CMD_STOCK, self.stock)
            if self.at_left <= 0:
                if self.stock > 0:
                    # ストック放出：GGを介さずATを再セット（ストックはGGでのみ増える）
                    self.stock -= 1
                    self.at_left = AT_INIT_GAMES
                    self.notice.append(f"ストック放出（残{self.stock}）")
                    self.send(CMD_STOCK, self.stock)
                    self.send(CMD_AT_GAMES, self.at_left)
                else:
                    self.state = ST_NORMAL
                    self.mode, self.mode_left, self.game_count = MODE_LOW, 0, 0
                    self.notice.append("AT終了")
                    self.send(CMD_STATE, ST_NORMAL)
            else:
                self.send(CMD_AT_GAMES, min(self.at_left, 255))

    def _enter_gg(self, cause: str) -> None:
        self.state = ST_GG
        self.gg_left = GG_GAMES
        self.stock = max(self.stock, 1)     # 突入時1個保証
        self.game_count = 0
        self.mode, self.mode_left = MODE_LOW, 0
        self.notice.append(f"GG突入({cause})")
        self.send(CMD_STATE, ST_GG)
        self.send(CMD_STOCK, self.stock)

    # -- 1ゲームの主制御シーケンス ------------------------------------------
    def play(self) -> dict:
        """1ゲーム。回転はMAXベットが成立したときだけなので、クレジット3枚が要る。

        足りないまま呼ぶのは呼び出し側の誤り（--manual では can_bet() で先に弾く）。
        """
        if not self.bet():
            raise RuntimeError(f"クレジット不足（{self.credit}枚）: "
                               f"MAXベット{BET}枚が成立しないので回転できない")
        self.lottery()
        self.hit_lottery()          # 当否はレバーONで確定（この先ぶれない）
        self.enshutsu_lottery()     # 確定した当否と内部モードから演出を抽選し、副制御へ渡す
        push = self.spin_start()
        # AT中は押し順ナビ（主制御が正解を指示）、通常時は遊技者のランダム押し
        navi = self.state != ST_NORMAL and self.flag == "押順ベル"
        self.send(CMD_NAVI, self.bell_answer if navi else 0xFF)
        order = self.bell_answer if self.state != ST_NORMAL else self.get_random() % 6
        self.judge(push, order)
        pay = self.payout()
        state_before = self.state
        self.update_state()
        self.send(CMD_GAME_END, self.game_count & 0xFF)
        # 遊技終了後に現在の遊技状態を伝える。0x50 は移行した瞬間しか出ないので、
        # 副制御は毎ゲームの終わりにこれを見て自分の写しを確定できる。
        self.send(CMD_STATE_NOTIFY, self.state)
        result = {
            "game": self.total_games,
            "state": state_before,
            "flag": self.flag,
            "prize": self.prize,
            "pay": pay,
            "diff": self.total_out - self.total_in,
            "notice": list(self.notice),
            "enshutsu": self.enshutsu,
            "freeze": self.freeze,
        }
        if self.panel is not None:
            self.panel.send_game(self, result)
        return result


STATE_NAME = {ST_NORMAL: "通常", ST_GG: "GG", ST_AT: "AT"}


# ---------------------------------------------------------------------------
# 7. 副制御（演出制御基板）
# ---------------------------------------------------------------------------

class SubBoard:
    """
    副制御基板。主制御から届く2バイトコマンドだけで動作する。

    - 主制御へ送信する手段を持たない（単方向）。
    - 演出を抽選しない。何を出すかは主制御が決めており、副制御は 0x22 で届いた
      予告パターン番号を ENSHUTSU_PATTERN で引いて絵に起こすだけ。乱数を持たない。
    - したがって主制御の内部モードを推測する必要も無い（主制御がモードを織り込んで
      抽選した結果が番号で届く）。内部状態そのものは相変わらず見えない。
    - 出力は演出イベント（dict）。on_eventに渡した関数へそのまま流れるので、
      WebSocket送信やOBSオーバーレイへの中継に差し替えられる。

    演出のトリガーは遊技者の操作に対応する次の4点。
        レバーON  … 0x30 リール回転。この時点までに届いた 0x22 / 0x23 に従って
                    レバーON時点の演出を出す（フリーズならここで出す）。
        第1停止   … 0x31/0x32/0x33 のうち1つ目に届いた停止コマンド
        第2停止   … 同2つ目
        第3停止   … 同3つ目
    lever は毎ゲーム出す（オーバーレイが停止演出の時刻を測る起点になる）。stop は
    プランにランクがある停止だけ出す。
    予告プランは [レバーON, 第1停止, 第2停止, 第3停止] の各時点で出すバナーのランク
    （白/青/緑/赤/金、None=何も出さない）。最終ランクへ向けて段階的に上がる
    （ステップアップ予告）か、どこか1点だけで出す。組み合わせは主制御が抽選済み。
    GG突入・ストック・上乗せ・AT終了は遊技状態の通知なので、操作トリガーとは別に
    状態移行コマンドで出す。

    遊技終了後には 0x42 状態通知が届く。移行の有無によらず毎ゲーム来るので、
    副制御はこれで自分が持つ遊技状態の写しを確定させる（演出は出さない）。
    """

    def __init__(self, on_event=None, panel=None):
        self.on_event = on_event
        # 試験用モニタ端子。主制御とは別系統で、副制御の内部を外へ出すだけ。
        # ここから主制御へ届く経路は無い（単方向は崩れない）。
        self.panel = panel
        self.events: list = []
        self.game = 0
        self.state = ST_NORMAL
        self.at_left = 0
        self.stock = 0
        self.rx = 0            # 受信コマンド数
        self.flag = "ハズレ"   # 今ゲームの内部当選（0x20で受信。演出の実行はレバーONまで保留）
        self.pattern = 0       # 今ゲームの予告パターン番号（0x22で受信）
        self.plan = [None] * 4 # それを展開した [レバーON, 第1停止, 第2停止, 第3停止] のランク
        self.freeze = 0        # 今ゲームのフリーズ段階（0x23で受信。0=ロックなし）
        self.stops = 0         # 今ゲームで受けた停止コマンド数（第n停止の n）

    # -- 演出イベント出力 ---------------------------------------------------
    def emit(self, kind: str, **kw) -> dict:
        ev = {"g": self.game, "type": kind}
        ev.update(kw)
        self.events.append(ev)
        if self.on_event:
            self.on_event(ev)
        if self.panel is not None:
            self.panel.sub_event(ev)
        return ev

    # -- コマンド受信 -------------------------------------------------------
    def recv(self, cmd: int) -> None:
        typ, data = cmd >> 8, cmd & 0xFF
        self.rx += 1

        if typ == CMD_POWER_ON:
            # 電源投入・ラムクリア。主制御のRAMが消えたので、こちらの写しも初期化する
            self.game = 0
            self.state = data
            self.at_left = 0
            self.stock = 0
            self._reset_game()

        elif typ == CMD_GAME_START:
            self.game += 1
            self.state = data
            self._reset_game()

        elif typ == CMD_FLAG:
            # 内部当選は覚えるだけ（コンパネ表示用）。何を出すかは 0x22 が決める
            self.flag = ID_FLAG.get(data, "ハズレ")

        elif typ == CMD_ENSHUTSU:
            # 主制御が抽選した予告パターン。番号を表で引いて今ゲームのプランにする
            self.pattern = data
            pat = ENSHUTSU_PATTERN[data] if data < len(ENSHUTSU_PATTERN) else (None,) * 4
            self.plan = [None if v is None else ENSHUTSU_RANK[v] for v in pat]

        elif typ == CMD_FREEZE:
            # 主制御が抽選したフリーズ段階（1〜3）。実行はレバーONで
            self.freeze = min(max(data, 0), len(FREEZE_SEQ))

        elif typ == CMD_REEL_START:
            self._on_lever()

        elif typ == CMD_NAVI and data != 0xFF:
            self.emit("navi", order=data)

        elif typ in (CMD_REEL_STOP_L, CMD_REEL_STOP_C, CMD_REEL_STOP_R):
            self.stops += 1
            if self.stops <= 3 and self.plan[self.stops]:
                # 第n停止。プランにランクがある時点だけ演出イベントを出す（無い停止は何も出さない）
                self.emit("stop", n=self.stops, rank=self.plan[self.stops])

        elif typ == CMD_STATE:
            if data == ST_GG and self.state != ST_GG:
                self.emit("gg_start")
            elif data == ST_NORMAL and self.state != ST_NORMAL:
                self.emit("at_end", total=self.stock)
            self.state = data

        elif typ == CMD_STOCK:
            if data > self.stock:
                self.emit("stock_up", stock=data, gain=data - self.stock)
            self.stock = data

        elif typ == CMD_AT_GAMES:
            self.at_left = data

        elif typ == CMD_ADD_GAMES:
            self.emit("add_games", games=data)

        elif typ == CMD_STATE_NOTIFY:
            # 遊技終了後の状態通知。移行の演出は 0x50 が出したあとなので、ここでは写しを合わせるだけ。
            self.state = data

        if self.panel is not None:
            # 状態通知は1ゲームに必ず1回、遊技終了の直後に来る。ここだけは無変化でも送り、
            # 主制御モニタと同じ1ゲーム周期の生存確認を保つ。
            self.panel.sub_state(self, force=(typ == CMD_STATE_NOTIFY))

    # -- 今ゲームのワークを初期化 -------------------------------------------
    def _reset_game(self) -> None:
        self.flag = "ハズレ"
        self.pattern = 0
        self.plan = [None] * 4
        self.freeze = 0
        self.stops = 0

    # -- レバーON: 受け取った演出を出す（ここでは抽選しない） --------------------
    def _on_lever(self) -> None:
        if self.freeze:
            # レバーONフリーズ。段階数は主制御が抽選済み
            # （ロック1=振動 → ロック2=カットイン → ロック3=暗転）
            self.plan = [None] * 4
            self.emit("freeze", seq=list(FREEZE_SEQ[:self.freeze]), rank="金")
            return
        # レバーON時点の演出。plan と pattern は試験用モニタ向けの参考情報
        # （オーバーレイは rank だけを見る）
        self.emit("lever", rank=self.plan[0], plan=list(self.plan),
                  trigger=self.flag, pattern=self.pattern)


# ---------------------------------------------------------------------------
# 8. 演出送信ブリッジ（副制御 → オーバーレイ）
#
#    副制御の出力先をWebSocket送信に差し替える。外部依存なし（標準ライブラリのみ）。
#    こちらも単方向：クライアントからの受信フレームは破棄し、遊技には一切影響しない。
# ---------------------------------------------------------------------------

WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


class EnshutsuServer:
    """演出イベントをWebSocketで配信する最小サーバ。OBSブラウザソースから接続する。"""

    def __init__(self, host: str = "127.0.0.1", port: int = 8765):
        self.host, self.port = host, port
        self.clients: list = []
        self.lock = threading.Lock()
        self.sock: socket.socket | None = None
        self.running = False

    def start(self) -> None:
        """副制御ポートを排他で確保する。すでに使われていれば OSError を投げる。

        Windows の SO_REUSEADDR は「使用中のポートにも bind できてしまう」ため、
        これを付けると主制御の二重起動を止められない。2つ目が黙ってポートを奪い、
        両方が中継サーバーへ流すので、1回のレバーONでリールが2回回る。
        排他バインド（Windows は SO_EXCLUSIVEADDRUSE）にして二重起動を弾く。
        直前に落とした主制御のクライアント接続が TIME_WAIT で残っていることがあるので、
        数百ミリ秒だけ待ち直す（本当の二重起動なら空かないので、そのまま失敗する）。
        """
        deadline = time.monotonic() + 2.0
        while True:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):      # Windows
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            else:                                            # POSIX は TIME_WAIT の再利用のみ
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((self.host, self.port))
            except OSError:
                sock.close()
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.2)
                continue
            break
        self.sock = sock
        self.sock.listen(8)
        self.running = True
        threading.Thread(target=self._accept_loop, daemon=True).start()

    def _accept_loop(self) -> None:
        while self.running:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            if self._handshake(conn):
                with self.lock:
                    self.clients.append(conn)
            else:
                conn.close()

    @staticmethod
    def _handshake(conn: socket.socket) -> bool:
        try:
            req = b""
            while b"\r\n\r\n" not in req:
                chunk = conn.recv(1024)
                if not chunk:
                    return False
                req += chunk
            key = ""
            for line in req.decode("latin-1").split("\r\n"):
                if line.lower().startswith("sec-websocket-key:"):
                    key = line.split(":", 1)[1].strip()
            if not key:
                return False
            accept = base64.b64encode(
                hashlib.sha1((key + WS_GUID).encode()).digest()).decode()
            conn.sendall(
                b"HTTP/1.1 101 Switching Protocols\r\n"
                b"Upgrade: websocket\r\nConnection: Upgrade\r\n"
                b"Sec-WebSocket-Accept: " + accept.encode() + b"\r\n\r\n")
            return True
        except OSError:
            return False

    @staticmethod
    def _frame(payload: bytes) -> bytes:
        head = bytearray([0x81])            # FIN + テキストフレーム
        n = len(payload)
        if n < 126:
            head.append(n)
        elif n < 1 << 16:
            head.append(126)
            head += n.to_bytes(2, "big")
        else:
            head.append(127)
            head += n.to_bytes(8, "big")
        return bytes(head) + payload        # サーバ→クライアントはマスクなし

    def broadcast(self, event: dict) -> None:
        """SubBoardのon_eventに渡す。接続が無ければ黙って捨てる。"""
        data = self._frame(json.dumps(event, ensure_ascii=False).encode())
        with self.lock:
            dead = []
            for c in self.clients:
                try:
                    c.sendall(data)
                except OSError:
                    dead.append(c)
            for c in dead:
                self.clients.remove(c)
                c.close()

    def close(self) -> None:
        self.running = False
        with self.lock:
            for c in self.clients:
                c.close()
            self.clients.clear()
        if self.sock:
            self.sock.close()


# ---------------------------------------------------------------------------
# 9. コンパネ通信（主制御 → 中継サーバー → main_control.html）
#
#    main_control.html と enshutsu_overlay.html は trigger_relay_server.js
#    （ws://127.0.0.1:8787）に接続し、{"action": ...} 形式のJSONを中継している。
#    主制御もそこへクライアントとして接続し、毎ゲームの内部情報を送る。
#    中継サーバーは受信メッセージを送信元以外の全クライアントへ転送するだけなので、
#    コンパネ側は data.action === "mainBoard" を拾えばよい。
#
#    送信するメッセージ（すべて action: "mainBoard"）
#      type: "state"    毎ゲームのレジスタダンプ（状態/モード/成立役/表示役/差枚/ストック…）
#      type: "event"    契機発生（gg_start / god / stock_up / at_start / add_games /
#                       stock_release / at_end）
#      type: "cmd"      主→副の2バイトコマンド生ログ（--panel-cmds 指定時のみ）
#      type: "summary"  停止時の集計
# ---------------------------------------------------------------------------

PANEL_URL = "ws://127.0.0.1:8787"

PANEL_EVENT = [                       # 主制御の通知文字列 → コンパネ向けイベント名
    ("GG突入(",      "gg_start"),
    ("神揃い",       "god"),
    ("赤7揃い",      "stock_up"),
    ("ストック+1",   "stock_up"),
    ("AT開始",       "at_start"),
    ("+30G",         "add_games"),
    ("ストック放出", "stock_release"),
    ("AT終了",       "at_end"),
]


class WsClient:
    """最小WebSocketクライアント（RFC6455テキストフレーム／標準ライブラリのみ）。
    接続できない・切れた場合は黙って捨て、一定間隔で再接続を試みる。

    on_message を渡すと受信スレッドが立ち、中継サーバー経由で届いたJSONを渡す。
    これは試験用モニタ端子（コンパネ）専用の経路であり、副制御ポートは
    従来どおり単方向のまま。副制御から主制御へ戻る手段は増えない。"""

    def __init__(self, url: str, retry_sec: float = 3.0, on_message=None):
        u = urlparse(url)
        self.url = url
        self.host = u.hostname or "127.0.0.1"
        self.port = u.port or 80
        self.path = u.path or "/"
        self.retry_sec = retry_sec
        self.on_message = on_message
        self.sock: socket.socket | None = None
        self._next_retry = 0.0

    def connect(self) -> bool:
        try:
            s = socket.create_connection((self.host, self.port), timeout=2.0)
            key = base64.b64encode(random.SystemRandom().randbytes(16)).decode()
            s.sendall((f"GET {self.path} HTTP/1.1\r\nHost: {self.host}:{self.port}\r\n"
                       "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                       f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
            buf = b""
            while b"\r\n\r\n" not in buf:
                chunk = s.recv(4096)
                if not chunk:
                    raise ConnectionError("handshake closed")
                buf += chunk
            if b" 101 " not in buf.split(b"\r\n", 1)[0]:
                raise ConnectionError("handshake rejected")
            s.settimeout(None)
            self.sock = s
            if self.on_message is not None:
                threading.Thread(target=self._rx_loop, args=(s,), daemon=True).start()
            print(f"[コンパネ] 中継サーバーに接続: {self.url}", file=sys.stderr)
            return True
        except OSError as e:
            self.sock = None
            self._next_retry = time.monotonic() + self.retry_sec
            print(f"[コンパネ] 未接続({e}) {self.retry_sec:.0f}秒後に再試行", file=sys.stderr)
            return False

    def _rx_loop(self, s: socket.socket) -> None:
        """中継サーバーからのテキストフレームを解いて on_message へ渡す。
        切断・不正フレームは黙って終了する（遊技には影響しない）。"""
        buf = b""

        def take(n: int) -> bytes:
            nonlocal buf
            while len(buf) < n:
                chunk = s.recv(4096)
                if not chunk:
                    raise ConnectionError("closed")
                buf += chunk
            out, buf = buf[:n], buf[n:]
            return out

        try:
            while True:
                head = take(2)
                opcode = head[0] & 0x0F
                masked = head[1] & 0x80
                n = head[1] & 0x7F
                if n == 126:
                    n = int.from_bytes(take(2), "big")
                elif n == 127:
                    n = int.from_bytes(take(8), "big")
                mask = take(4) if masked else b""
                body = take(n) if n else b""
                if masked:
                    body = bytes(b ^ mask[i & 3] for i, b in enumerate(body))
                if opcode == 0x8:          # close
                    return
                if opcode != 0x1:          # テキスト以外は無視
                    continue
                try:
                    self.on_message(json.loads(body.decode()))
                except (ValueError, UnicodeDecodeError):
                    pass
        except (OSError, ConnectionError):
            return

    def send(self, obj: dict) -> bool:
        if self.sock is None:
            if time.monotonic() < self._next_retry or not self.connect():
                return False
        payload = json.dumps(obj, ensure_ascii=False).encode()
        n = len(payload)
        head = bytearray([0x81])                     # FIN + テキスト
        if n < 126:
            head.append(0x80 | n)
        elif n < 1 << 16:
            head.append(0x80 | 126); head += n.to_bytes(2, "big")
        else:
            head.append(0x80 | 127); head += n.to_bytes(8, "big")
        mask = random.SystemRandom().randbytes(4)      # クライアント→サーバはマスク必須
        body = bytes(b ^ mask[i & 3] for i, b in enumerate(payload))
        try:
            self.sock.sendall(bytes(head) + mask + body)
            return True
        except OSError as e:
            print(f"[コンパネ] 送信失敗({e}) 切断扱い", file=sys.stderr)
            self.close()
            self._next_retry = time.monotonic() + self.retry_sec
            return False

    def close(self) -> None:
        if self.sock:
            try:
                self.sock.sendall(bytes([0x88, 0x80]) + random.SystemRandom().randbytes(4))
                self.sock.close()
            except OSError:
                pass
        self.sock = None


class PanelLink:
    """主制御の試験用モニタ端子。MainBoard.panel に差すと毎ゲーム自動で送信される。"""

    def __init__(self, url: str = PANEL_URL, raw_cmds: bool = False):
        # コンパネからの注入フレームは受信スレッドで届くが、そこで直接レジスタを
        # 触ると遊技中の処理と競合する。キューに積み、遊技スレッド側で取り出す。
        self.inject_q: "queue.Queue[dict]" = queue.Queue()
        self.ws = WsClient(url, on_message=self._on_message)
        self.raw_cmds = raw_cmds
        self._sub_last: dict | None = None

    def _on_message(self, msg) -> None:
        if isinstance(msg, dict) and msg.get("action") == "panelInject":
            self.inject_q.put(msg)

    def tap_cmd(self, cmd: int) -> None:
        if not self.raw_cmds:
            return
        typ, data = cmd >> 8, cmd & 0xFF
        note = ""
        if typ in (CMD_FLAG, CMD_ALL_STOP):
            note = ID_FLAG.get(data, "")
        elif typ in (CMD_GAME_START, CMD_STATE, CMD_STATE_NOTIFY, CMD_POWER_ON):
            note = STATE_NAME.get(data, "")
        elif typ == CMD_NAVI:
            note = "ナビなし" if data == 0xFF else f"押し順{data + 1}"
        elif typ == CMD_ENSHUTSU:
            note = enshutsu_note(data)
        elif typ == CMD_FREEZE:
            note = "ロックなし" if data == 0 else f"ロック{data}"
        self.ws.send({"action": "mainBoard", "type": "cmd", "cmd": f"0x{cmd:04X}",
                      "name": CMD_NAME.get(typ, "?"), "data": data, "note": note})

    def send_input(self, b: MainBoard, accept: bool, reason: str = "") -> None:
        """レバーONの受付状態。無効化した瞬間と明けた瞬間の2回だけ送る。

        残り時間を毎フレーム流すと中継サーバーのログが埋まるので、無効化のときに
        長さ(waitMs)を渡し、カウントダウンは受け側に任せる。"""
        self.ws.send({"action": "mainBoard", "type": "input",
                      "accept": accept,
                      "waitMs": 0 if accept else round(b.wait_left() * 1000),
                      "reason": reason, "game": b.total_games})

    def send_credit(self, b: MainBoard, note: str = "") -> None:
        """クレジット（貯留）。投入信号を受けたときと、ベットできずに弾いたときに送る。
        遊技中の増減は state に載るので、ここでは遊技の切れ目だけを知らせる。"""
        self.ws.send({"action": "mainBoard", "type": "credit", "credit": b.credit,
                      "canBet": b.credit >= BET, "bet": BET, "note": note,
                      "game": b.total_games})

    def send_ram_clear(self, b: MainBoard) -> None:
        """ラムクリア。消えたあとのレジスタを送り、コンパネの表示を初期状態へ戻す。"""
        self.ws.send({"action": "mainBoard", "type": "ramClear",
                      "setting": b.setting, "game": 0,
                      "state": STATE_NAME[b.state],
                      "mode": ["低確", "高確", "超高確"][b.mode],
                      "gameCount": b.game_count,
                      "ceilingLeft": CEILING - b.game_count,
                      "diff": 0, "totalIn": 0, "totalOut": 0,
                      "stock": b.stock, "credit": b.credit,
                      "canBet": b.credit >= BET, "bet": BET})

    def send_mode(self, b: MainBoard, manual: bool, waiting: bool) -> None:
        """遊技の進み方。manual なら主制御は自分から回さず、レバーON入力を待つ。

        waiting は「いま入力待ちで止まっている」ことを表す。待っている間は無風で
        何も送らないと、コンパネの生存監視が受信途絶と誤判定するため、
        run_live が数秒おきに送り直す。あとから開いたコンパネにも今の貯留が出るよう、
        クレジットもここに載せる。"""
        self.ws.send({"action": "mainBoard", "type": "mode", "manual": manual,
                      "waiting": waiting, "credit": b.credit,
                      "canBet": b.credit >= BET, "game": b.total_games})

    def send_game(self, b: MainBoard, r: dict) -> None:
        normal = b.state == ST_NORMAL
        self.ws.send({
            "action": "mainBoard", "type": "state",
            "game": r["game"],
            "setting": b.setting,
            "state": STATE_NAME[b.state],
            "mode": ["低確", "高確", "超高確"][b.mode] if normal else None,
            "gameCount": b.game_count if normal else None,
            "ceilingLeft": max(0, CEILING - b.game_count) if normal else None,
            "flag": r["flag"],
            "prize": r["prize"],
            "pay": r["pay"],
            "reel": [REELS[i][b.reel_pos[i]] for i in range(3)],
            "reelPos": list(b.reel_pos),          # 停止位置(コマ番号)。筐体側のリール停止再現用
            "navi": (b.bell_answer + 1) if (not normal and r["flag"] == "押順ベル") else None,
            "enshutsu": r["enshutsu"],
            "enshutsuNote": enshutsu_note(r["enshutsu"]),
            "freeze": r["freeze"],
            "diff": r["diff"],
            "credit": b.credit,
            "totalIn": b.total_in,
            "totalOut": b.total_out,
            "stock": b.stock,
            "ggLeft": b.gg_left if b.state == ST_GG else None,
            "atLeft": b.at_left if b.state == ST_AT else None,
        })
        for note in r["notice"]:
            ev = next((e for key, e in PANEL_EVENT if note.startswith(key)), "info")
            cause = note[note.find("(") + 1:note.rfind(")")] if "(" in note else None
            self.ws.send({"action": "mainBoard", "type": "event", "event": ev,
                          "cause": cause, "text": note, "game": r["game"],
                          "stock": b.stock, "atLeft": b.at_left})

    # -- 副制御の試験用モニタ端子 -------------------------------------------
    def sub_state(self, s: "SubBoard", force: bool = False) -> None:
        """副制御が受信内容だけから組み立てた状態。

        演出は主制御が抽選するので、ここに出るのは「副制御が受け取った演出」
        （パターン番号・展開したプラン・最終ランク・フリーズ段階）であって、
        副制御の見立てではない。主制御の内部モードは相変わらず届かない。

        1コマンドごとに呼ばれるが、中継サーバーは受信を全件ログ出力するため、
        中身が前回と変わったときだけ送る（rx と game は比較対象から外す）。
        ただし force のときは変化が無くても送る。通常時が無風だと状態が何分も
        動かず、コンパネ側の生存監視が受信途絶と誤判定するため。"""
        snap = {
            "pattern": s.pattern,
            "note": enshutsu_note(s.pattern),
            "plan": list(s.plan),
            "rank": next((r for r in reversed(s.plan) if r), None),
            "freeze": s.freeze,
            "state": STATE_NAME.get(s.state, "?"),
            "stock": s.stock, "atLeft": s.at_left,
        }
        if snap == self._sub_last and not force:
            return
        self._sub_last = snap
        self.ws.send({"action": "subBoard", "type": "state",
                      "rx": s.rx, "game": s.game, **snap})

    def sub_event(self, ev: dict) -> None:
        self.ws.send({"action": "subBoard", "type": "event", "event": ev})

    def send_summary(self, b: MainBoard) -> None:
        self.ws.send({"action": "mainBoard", "type": "summary", "setting": b.setting,
                      "games": b.total_games, "diff": b.total_out - b.total_in,
                      "totalIn": b.total_in, "totalOut": b.total_out})

    def close(self) -> None:
        self.ws.close()


# ---------------------------------------------------------------------------
# 10. 実行モード
# ---------------------------------------------------------------------------

def run_trace(board: MainBoard, games: int, interval: float = 0.0,
              manual: bool = False) -> None:
    """1G毎のログを表示する。manual（--manual）なら1ゲームずつEnter待ちで進める。"""
    step = manual and _stdin_is_tty()
    if manual and not step:
        print("[警告] 標準入力が端末ではないため --manual は無視し、続けて回します",
              file=sys.stderr)
    print(f"{'G':>5} {'状態':<4} {'成立役':<8} {'表示役':<8} {'払出':>4} {'差枚':>7}  通知")
    print("-" * 68)
    board.power_on()
    if step:
        print("Enter で1ゲーム進む（q で終了）")
    try:
        for _ in range(games):
            if step:
                try:
                    if input().strip().lower() in ("q", "quit", "exit"):
                        break
                except EOFError:
                    break
            r = board.play()
            note = " / ".join(r["notice"])
            print(f"{r['game']:>5} {STATE_NAME[r['state']]:<4} {r['flag']:<8} "
                  f"{r['prize']:<8} {r['pay']:>4} {r['diff']:>+7}  {note}")
            if interval > 0:
                time.sleep(interval)
    except KeyboardInterrupt:
        print("\n[停止]")


def run_commands(board: MainBoard, games: int) -> None:
    """主制御が送出する2バイトコマンドを生ログで表示する。"""
    board.power_on()
    for _ in range(games):
        board.play()
    print(f"{'CMD':>6}  {'種別':<12} データ")
    print("-" * 44)
    for cmd in board.cmd_log:
        typ, data = cmd >> 8, cmd & 0xFF
        note = ""
        if typ in (CMD_FLAG, CMD_ALL_STOP):
            note = ID_FLAG.get(data, "")
        elif typ in (CMD_GAME_START, CMD_STATE, CMD_STATE_NOTIFY):
            note = STATE_NAME.get(data, "")
        elif typ == CMD_NAVI:
            note = "ナビなし" if data == 0xFF else f"押し順{data + 1}"
        elif typ == CMD_ENSHUTSU:
            note = enshutsu_note(data)
        elif typ == CMD_FREEZE:
            note = "ロックなし" if data == 0 else f"ロック{data}"
        print(f"0x{cmd:04X}  {CMD_NAME.get(typ, '?'):<12} {data:>3}  {note}")


def run_events(board: MainBoard, games: int) -> None:
    """副制御が出力する演出イベントをJSON Linesで流す（外部中継用の形）。

    演出を抽選するのは主制御なので、--seed を固定すればここも再現する。"""
    board.sub = SubBoard(
        on_event=lambda ev: print(json.dumps(ev, ensure_ascii=False), flush=True),
    )
    board.power_on()
    try:
        for _ in range(games):
            board.play()
    except BrokenPipeError:
        pass


class LiveInput:
    """遊技を進める入力を1か所に集める（--manual 用）。

    実機のレバーは主制御への入力そのものなので、コンパネの「レバーON」と
    筐体ビューのレバー（どちらも panelInject layer:"lever"）、端末のEnterを
    同じ扱いにする。受信スレッドは put でキューに積むだけにし、値の反映（drain）は
    遊技スレッドで行う（レジスタを触る処理と競合させない）。
    """

    def __init__(self, manual: bool = False):
        self.manual = manual        # True … 主制御は自分から回さず、レバーONを待つ
        self.lever = 0              # 受け取ったレバーONの数（1つにつき1ゲーム進む）
        self.credit_in = 0          # クレジット投入信号で受け取った枚数（未反映ぶん）
        self.quit = False           # 端末から q が入った
        self.q: "queue.Queue[tuple[str, object]]" = queue.Queue()

    def put(self, kind: str, value: object = None) -> None:
        self.q.put((kind, value))

    def drain(self) -> None:
        while True:
            try:
                kind, value = self.q.get_nowait()
            except queue.Empty:
                return
            if kind == "lever":
                try:
                    n = int(value) if value is not None else 1
                except (TypeError, ValueError):
                    n = 1
                self.lever += max(1, min(n, 1000))
            elif kind == "credit":
                try:
                    n = int(value) if value is not None else 1
                except (TypeError, ValueError):
                    n = 1
                self.credit_in += max(1, min(n, 100000))
            elif kind == "mode":
                self.manual = bool(value)
                self.lever = 0          # 切り替え前の入力は持ち越さない
            elif kind == "quit":
                self.quit = True

    def take_lever(self) -> bool:
        """レバーONを1つ消費する。無ければ False。"""
        if self.lever <= 0:
            return False
        self.lever -= 1
        return True

    def take_credit(self) -> int:
        """受け取ったクレジット投入信号ぶんの枚数を取り出す（無ければ0）。"""
        n, self.credit_in = self.credit_in, 0
        return n


def _stdin_is_tty() -> bool:
    """標準入力が端末か。バックグラウンド起動では偽になる。"""
    try:
        return sys.stdin is not None and sys.stdin.isatty()
    except (AttributeError, ValueError):    # 標準入力が閉じている
        return False


def watch_stdin(inp: LiveInput) -> None:
    """端末から起動したときだけ、Enterをレバーオンとして拾う（a=自動 / m=手動 / q=終了）。

    scripts\\dev.cmd start のようなバックグラウンド起動では標準入力が端末では
    ないので何もしない。その場合の入力はコンパネと筐体ビューのレバーONだけになる。
    """
    if not _stdin_is_tty():
        return

    def loop() -> None:
        for line in sys.stdin:
            s = line.strip().lower()
            if s in ("q", "quit", "exit"):
                inp.put("quit")
                return
            if s in ("a", "auto"):
                inp.put("mode", False)
                print("[自動] 以後は主制御が自分で回します", file=sys.stderr)
            elif s in ("m", "manual"):
                inp.put("mode", True)
                print("[手動] レバーON待ちに戻します（Enterで1ゲーム）", file=sys.stderr)
            else:
                inp.put("lever", 1)

    threading.Thread(target=loop, daemon=True).start()


def drain_inject(board: MainBoard, srv: "EnshutsuServer",
                 inp: "LiveInput | None" = None) -> None:
    """コンパネから届いた注入フレームを遊技スレッド側で流し込む。

    main2sub … 主制御の送信口をそのまま使う。副制御は正規の受信と区別できず、
               コマンド生ログにも同じ形で残る。副制御のロジック検証用。
    enshutsu … 副制御の判断を飛ばしてオーバーレイへ直送する。表示確認用。
    lever    … レバーON（--manual のときだけ意味を持つ）。games で複数ゲーム分。
    credit   … クレジット投入信号。{"layer":"credit","n":50}（既定1枚・上限なし）
    mode     … 自動/手動の切り替え。{"layer":"mode","manual":true|false}
    ramClear … ラムクリア。主制御のRAMを初期化し、電源投入コマンドを送り直す。
    """
    if board.panel is None:
        return
    while True:
        try:
            msg = board.panel.inject_q.get_nowait()
        except queue.Empty:
            return
        layer = msg.get("layer")
        try:
            if layer == "main2sub":
                board.send(int(msg.get("type", 0)), int(msg.get("data", 0)))
            elif layer == "enshutsu":
                ev = msg.get("event")
                if isinstance(ev, dict):
                    srv.broadcast(ev)
                    board.panel.sub_event(ev)
            elif layer == "lever" and inp is not None:
                inp.put("lever", msg.get("games", 1))
            elif layer == "credit" and inp is not None:
                inp.put("credit", msg.get("n", 1))
            elif layer == "mode" and inp is not None:
                inp.put("mode", msg.get("manual", True))
            elif layer == "ramClear":
                # 遊技スレッドから呼ぶので、遊技中の処理と競合しない
                board.ram_clear()
                board.panel.send_ram_clear(board)
                print("ラムクリア: 主制御のRAMを初期化しました", file=sys.stderr)
        except (TypeError, ValueError):
            pass


def run_live(board: MainBoard, games: int, host: str, port: int,
             interval: float, freeze_hold: float = 0.0, manual: bool = False,
             credit_refill: bool = False) -> None:
    """演出イベントをWebSocketで配信しながら稼働させる。

    遊技の周期はウェイトが決める。前回の回転開始から interval 秒たつまでは
    レバーONを受け付けず、明けてから play()（=回転開始）に入る。したがって
    周期は max(interval, 実際の遊技時間) になり、固定スリープの加算にはならない。

    manual（--manual）のときは主制御が自分から回さない。起動直後も含め、毎ゲーム
    レバーON入力を待ってから play() に入る。入力はコンパネの「レバーON」、
    筐体ビューのレバー（Space/Enter・レバー欄のクリック）、端末のEnterのいずれか。
    ウェイトは手動でも生きている（無効時間中のレバーONは実機と同じく効かない）。

    クレジットは自動・手動のどちらでも実機どおり要る。MAXベット（規定投入枚数3枚）が
    成立しないと回らないので、先にクレジット投入信号を送る（コンパネの「🪙投入」／
    --credit で起動時に入れておく）。自動のときは貯留が尽きた時点で投入信号を待って止まる。
    メダルが無限になるのは集計・検証モード（--games / --ladder / --sim / --commands /
    --events / --trace）と、--credit-refill を付けた検証用の稼働だけで、
    ふだんの稼働（--serve）では補充しない。
    """
    srv = EnshutsuServer(host, port)
    try:
        srv.start()
    except OSError as e:
        # 二重起動。ここで止めないと2台ぶんの演出とレジスタが中継サーバーへ流れ、
        # 1回のレバーONでリールが2回回るなど、実機ではありえない動きになる。
        print(f"[エラー] 副制御ポート {host}:{port} を確保できません: {e}", file=sys.stderr)
        print("        主制御がすでに起動しています（二重起動）。"
              "先に scripts\\dev.cmd stop で止めてから起動してください。", file=sys.stderr)
        return
    print(f"演出配信中: ws://{host}:{port}  （Ctrl+Cで停止）", file=sys.stderr)
    board.wait_time = max(interval, 0.0)
    board.sub = SubBoard(on_event=srv.broadcast, panel=board.panel)
    board.power_on()

    inp = LiveInput(manual=manual)
    # 稼働中は自動・手動のどちらでもクレジットが要る（実機と同じく貯留が尽きたら回らない）。
    # 演出の動作確認で貯留を気にしたくないときだけ --credit-refill で補充を有効にする。
    board.auto_insert = credit_refill
    watch_stdin(inp)
    if manual:
        print(f"手動: レバーON待ち（コンパネ／筐体ビューのレバー、端末ならEnterで1G。"
              f"a=自動 m=手動 q=終了）／クレジット {board.credit}枚", file=sys.stderr)
        if board.panel is None and not _stdin_is_tty():
            print("[警告] コンパネへ繋がらず(--no-panel)、標準入力も端末ではないため、"
                  "レバーONを受け取る経路がありません", file=sys.stderr)

    def notify(accept: bool, reason: str = "") -> None:
        if board.panel is not None:
            board.panel.send_input(board, accept, reason)

    def notify_mode(waiting: bool) -> None:
        if board.panel is not None:
            board.panel.send_mode(board, inp.manual, waiting)

    def pump() -> None:
        drain_inject(board, srv, inp)   # コンパネからの注入・レバーON・投入・自動手動切替
        inp.drain()                     # 端末のEnterを含め、受け取った入力を反映
        n = inp.take_credit()
        if n:                           # クレジット投入信号（最大クレジットは無限）
            board.insert_credit(n)
            if board.panel is not None:
                board.panel.send_credit(board, f"投入 +{n}")

    try:
        notify_mode(False)              # 起動直後の進み方をコンパネへ知らせる
        if board.panel is not None:
            board.panel.send_credit(board, "起動時")
        played = 0
        while played < games and not inp.quit:
            # [1] レバーON無効時間。明けるまで遊技を始めない（注入はこの間も拾う）
            if not board.accepts_lever():
                held = inp.lever        # 無効時間に入る前に受け取っていたぶんは残す
                notify(False, "フリーズ" if board.hold_until > board.spin_at + board.wait_time
                              else "ウェイト")
                while not board.accepts_lever() and not inp.quit:
                    pump()
                    time.sleep(0.05)
                inp.lever = min(inp.lever, held)   # 無効時間中のレバーONは効かない（実機と同じ）
                notify(True)
            # [2] 手動モード。レバーON入力が来るまで回さない（起動直後もここで止まる）。
            #     クレジットが規定投入枚数に満たないときは、叩かれても回らない。
            if inp.manual:
                pump()
                waiting = False
                last = time.monotonic()
                while inp.manual and not inp.quit:
                    if inp.take_lever():
                        if board.can_bet():
                            break                       # MAXベット成立 → 回す
                        if board.panel is not None:     # 実機と同じくレバーONは無効
                            board.panel.send_credit(board, f"クレジット不足（MAXベット{BET}枚）")
                        print(f"レバーON: クレジット不足（{board.credit}枚）", file=sys.stderr)
                    if not waiting:
                        waiting = True
                        notify_mode(True)
                        last = time.monotonic()
                    time.sleep(0.02)
                    pump()
                    if time.monotonic() - last >= 5.0:
                        notify_mode(True)   # 無風でも生存を示す（コンパネの受信途絶よけ）
                        last = time.monotonic()
                if waiting:
                    notify_mode(False)
            if inp.quit:
                break
            pump()
            # [3] クレジット。自動でも実機と同じく貯留が要る。足りなければ投入信号を待つ
            #     （待っている間に手動へ切り替えられたらループの頭へ戻る）。
            if not board.can_bet():
                if board.panel is not None:
                    board.panel.send_credit(board, f"クレジット不足（MAXベット{BET}枚）")
                print(f"クレジット不足（{board.credit}枚）: 投入信号待ち", file=sys.stderr)
                while not board.can_bet() and not inp.quit and not inp.manual:
                    pump()
                    time.sleep(0.05)
                if inp.quit:
                    break
                if inp.manual:
                    continue
                if board.panel is not None:
                    board.panel.send_credit(board, "投入されました")
            r = board.play()
            played += 1
            if freeze_hold > 0.0 and r["freeze"]:
                board.hold_lever(freeze_hold)   # フリーズぶん回転開始を止める
    except KeyboardInterrupt:
        pass
    finally:
        srv.close()
        d = board.total_out - board.total_in
        print(f"停止: {board.total_games:,}G / 差枚 {d:+,}枚", file=sys.stderr)


def run_single(board: MainBoard, games: int) -> None:
    at_games = gg_hit = god_hit = 0
    for _ in range(games):
        r = board.play()
        if r["state"] != ST_NORMAL:
            at_games += 1
        for n in r["notice"]:
            if n.startswith("GG突入") and r["state"] == ST_NORMAL:
                gg_hit += 1
            if n == "神揃い":
                god_hit += 1
    diff = board.total_out - board.total_in
    rate = board.total_out / board.total_in * 100 if board.total_in else 0
    print(f"設定{board.setting} / {games:,}G")
    print(f"  差枚数     : {diff:+,} 枚")
    print(f"  機械割     : {rate:.1f} %")
    print(f"  GG初当り   : {gg_hit} 回" +
          (f"（1/{games / gg_hit:.0f}）" if gg_hit else ""))
    print(f"  神揃い     : {god_hit} 回")
    print(f"  AT稼働率   : {at_games / games * 100:.1f} %")
    if games < 100_000:
        print(f"  ※ {games:,}Gでは機械割が±10%以上ぶれる。"
              "設定差の確認は --ladder を使うこと")


def run_sim(setting: int, machines: int, games: int) -> None:
    diffs = []
    for i in range(machines):
        b = MainBoard(setting=setting, rng=random.Random(i), draw_enshutsu=False)
        for _ in range(games):
            b.play()
        diffs.append(b.total_out - b.total_in)
    diffs.sort()
    n = len(diffs)
    avg = sum(diffs) / n
    print(f"設定{setting} / {games:,}G × {machines:,}台")
    print(f"  平均差枚 : {avg:+,.0f} 枚   (機械割 {100 + avg / (games * BET) * 100:.1f}%)")
    print(f"  中央値   : {diffs[n // 2]:+,} 枚")
    print(f"  最低/最高: {diffs[0]:+,} / {diffs[-1]:+,} 枚")
    print(f"  プラス台 : {sum(1 for d in diffs if d > 0) / n * 100:.1f} %")
    print(f"  +3000枚超: {sum(1 for d in diffs if d > 3000) / n * 100:.1f} %")


def _ladder_job(arg: tuple) -> tuple:
    """--ladder のワーカー1本。1台分を回して (投入, 払出, 状態別G数, GG初当り) を返す。"""
    setting, seed, games = arg
    b = MainBoard(setting=setting, rng=random.Random(seed),
                  log_cmds=False, draw_enshutsu=False)
    st = [0, 0, 0]
    hit = 0
    for _ in range(games):
        before = b.state
        r = b.play()
        st[before] += 1
        if before == ST_NORMAL and any(n.startswith("GG突入") for n in r["notice"]):
            hit += 1
    return b.total_in, b.total_out, st, hit


def run_ladder(games: int, seed: int | None = None) -> None:
    """
    設定1〜6の機械割をまとめて測り、設定間の逆転が無いかを確認する。

    抽選テーブルを触ったら必ずこれで測り直す。単発の集計（--games 10000 など）は
    神揃い5ストックの当否だけで±10%以上動くので、設定差の判断には使えない。
    """
    chunks = 4                      # 1設定を何本のプロセスに分けるか
    per = max(1, games // chunks)
    base = 0 if seed is None else seed
    # 種は (base, 設定, 通し番号) を混ぜて作る。--seed を1違えるだけで全系列が入れ替わる
    jobs = [(s, random.Random(f"{base}/{s}/{i}").randrange(1 << 32), per)
            for s in range(1, 7) for i in range(chunks)]
    try:
        from multiprocessing import Pool
        with Pool(min(len(jobs), os.cpu_count() or 1)) as pool:
            res = pool.map(_ladder_job, jobs)
    except (OSError, ValueError, ImportError):     # プロセスを作れない環境
        res = [_ladder_job(j) for j in jobs]

    print(f"設定1〜6 機械割ラダー（各設定 {per * chunks:,}G）")
    # 見出しは全角の表示幅に合わせて手で詰めてある（列幅 4 / 11 / 10 / 10 / 11）
    print("設定     機械割  前設定差    AT滞在   GG初当り")
    print("-" * 46)
    rates = []
    for i, s in enumerate(range(1, 7)):
        c = res[i * chunks:(i + 1) * chunks]
        tin = sum(x[0] for x in c)
        tout = sum(x[1] for x in c)
        st = [sum(x[2][k] for x in c) for k in range(3)]
        hit = sum(x[3] for x in c)
        n = sum(st)
        rate = tout / tin * 100
        diff = "-" if not rates else f"{rate - rates[-1]:+.2f}"
        first = "1/" + format(round(n / hit) if hit else 0, ",")
        rates.append(rate)
        print(f"{s:>4}{rate:>10.2f}%{diff:>10}{st[2] / n * 100:>9.1f}%{first:>11}")

    bad = [s for s in range(2, 7) if rates[s - 1] <= rates[s - 2]]
    if bad:
        pairs = ", ".join(f"設定{s - 1}→{s}" for s in bad)
        print(f"⚠ {pairs} で機械割が上がっていない。抽選テーブルを見直すこと")
    else:
        print("OK: 設定1→6で機械割は単調増加（逆転なし）")


def main() -> None:
    ap = argparse.ArgumentParser(description="GODタイプ主制御シミュレータ")
    ap.add_argument("--setting", type=int, default=1, choices=range(1, 7))
    ap.add_argument("--games", type=int, default=None,
                    help="集計ゲーム数。既定 10000（--ladder のときは設定あたり500万）")
    ap.add_argument("--trace", type=int, default=0, help="1G毎のログをNゲーム分表示")
    ap.add_argument("--sim", type=int, default=0, help="N台分の分布を集計")
    ap.add_argument("--ladder", action="store_true",
                    help="設定1〜6の機械割をまとめて測り、設定間の逆転を確認する")
    ap.add_argument("--commands", type=int, default=0, help="主→副コマンドをNゲーム分表示")
    ap.add_argument("--events", type=int, default=0, help="副制御の演出イベントをJSONで出力")
    ap.add_argument("--serve", action="store_true", help="演出イベントをWebSocketで配信")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--interval", type=float, default=WAIT_TIME,
                    help=f"ウェイト＝レバーON無効時間（秒）。回転開始から計る。"
                         f"既定は実機ウェイトの{WAIT_TIME}秒")
    ap.add_argument("--freeze-hold", type=float, default=0.0,
                    help="フリーズ（レバーONロック）の間、回転開始をさらに止める秒数"
                         "（既定0＝止めない）")
    ap.add_argument("--credit", type=int, default=0,
                    help="起動時のクレジット枚数（既定0）。--serve は自動・手動とも貯留が要る。"
                         "ベットはMAXベット固定で、"
                         f"--manual では{BET}枚無いと回せない（投入信号で足す）")
    ap.add_argument("--credit-refill", action="store_true",
                    help="クレジットが足りないとき自動で補充する（検証用）。"
                         "--serve の自動操作で貯留を気にせず回したいときに付ける。"
                         "集計・検証モードは指定に関わらず補充する")
    ap.add_argument("--manual", action="store_true",
                    help="起動しても自分から回さず、レバーON入力を待つ（--serve / --trace）。"
                         "入力はコンパネの「レバーON」・筐体ビューのレバー・端末のEnter")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--panel", default=PANEL_URL,
                    help=f"コンパネ中継サーバー(trigger_relay_server.js)のURL。既定 {PANEL_URL}")
    ap.add_argument("--no-panel", action="store_true", help="コンパネへの送信を行わない")
    ap.add_argument("--panel-cmds", action="store_true",
                    help="主→副の2バイトコマンド生ログもコンパネへ送る")
    a = ap.parse_args()
    games = a.games if a.games is not None else (5_000_000 if a.ladder else 10000)
    if a.manual and not (a.serve or a.trace):
        ap.error("--manual は --serve か --trace と一緒に使う（集計モードでは意味を持たない）")

    if a.ladder:
        run_ladder(games, a.seed)
        return
    if a.sim:
        run_sim(a.setting, a.sim, games)
        return
    board = MainBoard(
        setting=a.setting, rng=random.Random(a.seed), credit=max(0, a.credit),
        # 演出用の乱数は出玉抽選と別系統。--seed を与えたときだけ再現できるようにする
        enshutsu_rng=random.Random(None if a.seed is None else f"{a.seed}/enshutsu"))
    # --serve / --trace のときだけコンパネへ送る（集計モードでは送らない）
    if not a.no_panel and (a.serve or a.trace):
        board.panel = PanelLink(a.panel, raw_cmds=a.panel_cmds)
    try:
        if a.serve:
            run_live(board, games, a.host, a.port, a.interval,
                     freeze_hold=a.freeze_hold, manual=a.manual,
                     credit_refill=a.credit_refill)
        elif a.commands:
            run_commands(board, a.commands)
        elif a.events:
            run_events(board, a.events)
        elif a.trace:
            run_trace(board, a.trace, interval=0.0, manual=a.manual)
        else:
            run_single(board, games)
    finally:
        if board.panel:
            board.panel.send_summary(board)
            board.panel.close()


if __name__ == "__main__":
    main()
