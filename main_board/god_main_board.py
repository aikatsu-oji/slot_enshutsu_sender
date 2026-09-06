#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GODタイプ パチスロ機 主制御（メイン基板）シミュレータ

実機の主制御が1ゲームで実行する処理シーケンスを、そのまま関数単位に分解して再現する。

    [1] メダル投入 / BET      -> bet()
    [2] 乱数取得（16bit）      -> get_random()
    [3] 内部抽選（役決定）      -> lottery()
    [4] リール回転開始         -> spin_start()
    [5] 停止制御（引込み/蹴り） -> stop_reel()
    [6] 入賞判定（有効ライン）  -> judge()
    [7] 払出                  -> payout()
    [8] 状態遷移（AT/RT管理）   -> update_state()

本機はメイン管理AT（6号機準拠）として実装する。すなわちAT状態・ゲーム数・
ストックはすべて主制御が保持し、副制御（演出基板）には結果を通知するだけとする。

実在の遊技機の仕様値ではなく、GODタイプに共通する構造を模したオリジナル諸元。

出力ポートは2系統:
    副制御ポート   … 2バイトコマンド（単方向）。--serve で ws://127.0.0.1:8765 から
                    演出イベントとしてオーバーレイへ配信される。
    試験用モニタ端子 … 主制御の全レジスタ。trigger_relay_server.js（ws://127.0.0.1:8787）
                    へクライアント接続し、コンパネ main_control.html へ流す（第8節）。

usage:
    python3 god_main_board.py --setting 1 --games 10000   # 集計のみ（コンパネ送信なし）
    python3 god_main_board.py --trace 30                  # 1G毎ログ＋コンパネ送信
    python3 god_main_board.py --serve --games 1000        # 実機ウェイトで稼働。副→オーバーレイ、主→コンパネ
    python3 god_main_board.py --serve --panel-cmds        # 2バイトコマンド生ログもコンパネへ
    python3 god_main_board.py --serve --no-panel          # コンパネ送信を切る
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
    "弱チェリー": 2,
    "強チェリー": 2,
    "チャンス目": 0,
    "ハズレ": 0,
}

# 内部抽選テーブル（分母65536・設定1〜6）
# 押順ベルは6択（正解は1/6）。数値は主制御の抽選値そのものを模す。
LOTTERY_TABLE = {
    #  役          設定1   設定2   設定3   設定4   設定5   設定6
    "神揃い":     (    8,      8,      8,      9,      9,     11),
    "押順ベル":   (40000,  40000,  40000,  40000,  40000,  40000),
    "共通ベル":   (  260,    266,    272,    285,    295,    320),
    "リプレイ":   ( 6000,   6000,   6000,   6000,   6000,   6000),
    "スイカ":     (  650,    660,    675,    700,    720,    780),
    "弱チェリー": (  760,    770,    780,    800,    820,    880),
    "強チェリー": (  190,    195,    200,    215,    225,    250),
    "チャンス目": (  360,    370,    385,    405,    425,    470),
}

# ---------------------------------------------------------------------------
# 3. 状態定義
# ---------------------------------------------------------------------------

ST_NORMAL, ST_GG, ST_AT = 0, 1, 2          # 遊技状態
MODE_LOW, MODE_HIGH, MODE_SHIGH = 0, 1, 2  # 通常時の内部モード（低確/高確/超高確）

CEILING = 1200        # 天井ゲーム数
GG_GAMES = 10         # ゴッドゲームの固定ゲーム数
AT_INIT_GAMES = 50    # ストック1個あたりの初期ATゲーム数

# レア役からのGG（ゴッドゲーム）当選率  [低確, 高確, 超高確]
GG_RATE = {
    "スイカ":     (0.045, 0.130, 0.360),
    "弱チェリー": (0.018, 0.055, 0.170),
    "強チェリー": (0.110, 0.270, 0.560),
    "チャンス目": (0.160, 0.360, 0.750),
}

# モード昇格率（レア役成立時）  -> (高確, 超高確)
MODE_UP = {
    "スイカ":     (0.250, 0.020),
    "弱チェリー": (0.150, 0.010),
    "強チェリー": (0.400, 0.080),
    "チャンス目": (0.450, 0.100),
}
MODE_GAMES = 32       # 高確以上の滞在ゲーム数

GG_SEVEN_RATE = 0.185  # GG中1Gあたりの赤7揃い（ストック+1）
GG_GOD_RATE = 0.005    # GG中1Gあたりの神揃い（ストック+5）
GOD_STOCK = 5         # 神揃い時の獲得ストック


# ---------------------------------------------------------------------------
# 4. 主制御 → 副制御 コマンド（単方向シリアル・2バイト）
#
#    上位バイト = コマンド種別 / 下位バイト = データ。
#    通信は主→副の一方向のみ。副制御は主制御へ一切送信できず、
#    主制御は副制御の状態を参照しない（副が落ちても遊技は続行する）。
#    内部モード（低確/高確/超高確）は送信しない。副制御は受信した
#    情報だけから独自に高確度を推測して演出を決める。
# ---------------------------------------------------------------------------

CMD_POWER_ON = 0x01     # 電源投入・復帰
CMD_MEDAL_IN = 0x10     # メダル投入   data: 投入枚数
CMD_GAME_START = 0x11   # 遊技開始     data: 遊技状態
CMD_FLAG = 0x20         # 内部当選     data: 条件装置番号
CMD_NAVI = 0x21         # 押し順ナビ   data: 押し順番号(0-5) / 0xFF=ナビなし
CMD_REEL_START = 0x30   # 全リール回転開始
CMD_REEL_STOP_L = 0x31  # 左リール停止 data: 停止位置(コマ番号)
CMD_REEL_STOP_C = 0x32
CMD_REEL_STOP_R = 0x33
CMD_ALL_STOP = 0x34     # 全停止       data: 表示役番号
CMD_PAYOUT = 0x40       # 払出         data: 払出枚数
CMD_GAME_END = 0x41     # 遊技終了     data: 通常時ゲーム数(下位8bit)
CMD_STATE = 0x50        # 状態移行     data: 遊技状態
CMD_STOCK = 0x51        # ストック数   data: 個数
CMD_AT_GAMES = 0x52     # AT残ゲーム数 data: 残G(下位8bit)
CMD_ADD_GAMES = 0x53    # 上乗せ       data: 上乗せG数

CMD_NAME = {
    CMD_POWER_ON: "電源投入", CMD_MEDAL_IN: "メダル投入", CMD_GAME_START: "遊技開始",
    CMD_FLAG: "内部当選", CMD_NAVI: "押し順ナビ", CMD_REEL_START: "リール回転",
    CMD_REEL_STOP_L: "左リール停止", CMD_REEL_STOP_C: "中リール停止",
    CMD_REEL_STOP_R: "右リール停止", CMD_ALL_STOP: "全停止", CMD_PAYOUT: "払出",
    CMD_GAME_END: "遊技終了", CMD_STATE: "状態移行", CMD_STOCK: "ストック数",
    CMD_AT_GAMES: "AT残G", CMD_ADD_GAMES: "上乗せ",
}

# 条件装置番号（主副で共有する定数）
FLAG_ID = {name: i for i, name in enumerate(
    ["ハズレ", "リプレイ", "共通ベル", "押順ベル", "こぼし", "スイカ",
     "弱チェリー", "強チェリー", "チャンス目", "赤7揃い", "神揃い"])}
ID_FLAG = {v: k for k, v in FLAG_ID.items()}

RARE = ("スイカ", "弱チェリー", "強チェリー", "チャンス目")


# ---------------------------------------------------------------------------
# 5. 主制御の内部レジスタ
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

    # 副制御ポート（単方向送信専用）
    sub: "SubBoard | None" = None
    cmd_log: list = field(default_factory=list)

    # 試験用モニタ端子（コンパネ main_control.html 向け）。副制御とは別ポートで、
    # 内部モードを含む全レジスタを出す。遊技には一切影響しない（送信失敗は無視）。
    panel: "PanelLink | None" = None

    # -- [0] 主制御 → 副制御 送信 ------------------------------------------
    def send(self, cmd_type: int, data: int = 0) -> None:
        """2バイトコマンドを副制御へ送出する。戻り値は受け取らない（単方向）。"""
        cmd = ((cmd_type & 0xFF) << 8) | (data & 0xFF)
        self.cmd_log.append(cmd)
        if self.sub is not None:
            self.sub.recv(cmd)
        if self.panel is not None:
            self.panel.tap_cmd(cmd)

    def power_on(self) -> None:
        self.send(CMD_POWER_ON, self.state)

    # -- [1] メダル投入 ----------------------------------------------------
    def bet(self) -> None:
        self.total_in += BET
        self.total_games += 1
        self.send(CMD_MEDAL_IN, BET)
        self.send(CMD_GAME_START, self.state)

    # -- [2] 乱数取得 ------------------------------------------------------
    def get_random(self) -> int:
        """16bitハードウェア乱数（0-65535）を1つラッチする。"""
        return self.rng.randrange(65536)

    # -- [3] 内部抽選 ------------------------------------------------------
    def lottery(self) -> str:
        r = self.get_random()
        acc = 0
        idx = self.setting - 1
        for name, values in LOTTERY_TABLE.items():
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

    # -- [4] リール回転 ----------------------------------------------------
    def spin_start(self) -> list:
        """各リールの目押し位置（=遊技者の停止操作位置）を決める。"""
        self.send(CMD_REEL_START)
        return [self.get_random() % KOMA for _ in range(3)]

    # -- [5] 停止制御 ------------------------------------------------------
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

    # -- [6] 入賞判定 ------------------------------------------------------
    def judge(self, push: list, order: int) -> str:
        """
        有効ライン（中段一直線）の図柄組合せから入賞役を確定する。
        押し順ベルはorder（遊技者の押し順）が正解と一致した場合のみ入賞。
        """
        flag = self.flag
        target = {
            "神揃い": "神", "赤7揃い": "赤7", "リプレイ": "リプ",
            "共通ベル": "ベル", "押順ベル": "ベル",
            "スイカ": "スイカ", "弱チェリー": "チェリー", "強チェリー": "チェリー",
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
        elif flag in ("弱チェリー", "強チェリー") and line[0] == "チェリー":
            self.prize = flag       # チェリーは左リール単独入賞
        else:
            self.prize = "ハズレ" if flag in ("チャンス目", "ハズレ") else "ハズレ"
            if flag == "チャンス目":
                self.prize = "チャンス目"
        self.send(CMD_ALL_STOP, FLAG_ID[self.prize])
        return self.prize

    # -- [7] 払出 ----------------------------------------------------------
    def payout(self) -> int:
        p = PAYOUT.get(self.prize, 0)
        self.total_out += p
        self.send(CMD_PAYOUT, p)
        return p

    # -- [8] 状態遷移 ------------------------------------------------------
    def update_state(self) -> None:
        flag = self.flag
        self.notice = []

        if flag == "神揃い":
            self.stock += GOD_STOCK
            self.notice.append("神揃い")
            self.send(CMD_STOCK, self.stock)
            self._enter_gg("神揃い")
            return

        if self.state == ST_NORMAL:
            self.game_count += 1
            if flag in GG_RATE and self.rng.random() < GG_RATE[flag][self.mode]:
                self._enter_gg(flag)
                return
            if flag in MODE_UP:
                hi, shi = MODE_UP[flag]
                r = self.rng.random()
                if r < shi:
                    self.mode, self.mode_left = MODE_SHIGH, MODE_GAMES
                elif r < shi + hi:
                    self.mode = max(self.mode, MODE_HIGH)
                    self.mode_left = MODE_GAMES
            if self.mode_left > 0:
                self.mode_left -= 1
                if self.mode_left == 0:
                    self.mode = MODE_LOW
            if self.game_count >= CEILING:
                self._enter_gg("天井")

        elif self.state == ST_GG:
            r = self.rng.random()
            if r < GG_GOD_RATE:
                self.stock += GOD_STOCK
                self.notice.append("神揃い")
                self.send(CMD_STOCK, self.stock)
            elif r < GG_GOD_RATE + GG_SEVEN_RATE:
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
            if flag == "チャンス目" and self.rng.random() < 0.35:
                self.at_left += 30
                self.notice.append("+30G")
                self.send(CMD_ADD_GAMES, 30)
            elif flag == "スイカ" and self.rng.random() < 0.20:
                self.stock += 1
                self.notice.append("ストック+1")
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
        self.bet()
        self.lottery()
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
        result = {
            "game": self.total_games,
            "state": state_before,
            "flag": self.flag,
            "prize": self.prize,
            "pay": pay,
            "diff": self.total_out - self.total_in,
            "notice": list(self.notice),
        }
        if self.panel is not None:
            self.panel.send_game(self, result)
        return result


STATE_NAME = {ST_NORMAL: "通常", ST_GG: "GG", ST_AT: "AT"}


# ---------------------------------------------------------------------------
# 6. 副制御（演出制御基板）
# ---------------------------------------------------------------------------

BANNER_RANK = ["白", "青", "緑", "赤", "金"]


class SubBoard:
    """
    副制御基板。主制御から届く2バイトコマンドだけで動作する。

    - 主制御へ送信する手段を持たない（単方向）。
    - 主制御の内部モードは受信できないため、レア役の受信履歴から
      自前のヒートカウンタで高確度を推測して演出頻度を決める。
    - 演出抽選には主制御とは別系統の乱数を使う（出玉に影響しない）。
    - 出力は演出イベント（dict）。on_eventに渡した関数へそのまま流れるので、
      WebSocket送信やOBSオーバーレイへの中継に差し替えられる。
    """

    def __init__(self, rng: random.Random | None = None, on_event=None, panel=None):
        self.rng = rng or random.Random()
        self.on_event = on_event
        # 試験用モニタ端子。主制御とは別系統で、副制御の内部を外へ出すだけ。
        # ここから主制御へ届く経路は無い（単方向は崩れない）。
        self.panel = panel
        self.events: list = []
        self.game = 0
        self.state = ST_NORMAL
        self.at_left = 0
        self.stock = 0
        self.heat = 0          # 副制御が独自に持つ高確示唆カウンタ
        self.rx = 0            # 受信コマンド数

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

        if typ == CMD_GAME_START:
            self.game += 1
            self.state = data

        elif typ == CMD_FLAG:
            self._on_flag(ID_FLAG.get(data, "ハズレ"))

        elif typ == CMD_NAVI and data != 0xFF:
            self.emit("navi", order=data)

        elif typ == CMD_ALL_STOP:
            if ID_FLAG.get(data) == "神揃い":
                # 3段階のフリーズ演出：ロック1（振動）→ロック2（カットイン）→ロック3（暗転）
                self.emit("freeze", seq=["lock1", "lock2", "lock3"], rank="金")

        elif typ == CMD_STATE:
            if data == ST_GG and self.state != ST_GG:
                self.emit("gg_start")
                self.heat = 0
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

        elif typ == CMD_GAME_END:
            if self.heat > 0:
                self.heat -= 1

        if self.panel is not None:
            # 遊技終了は1ゲームに必ず1回来る。ここだけは無変化でも送り、
            # 主制御モニタと同じ1ゲーム周期の生存確認を保つ。
            self.panel.sub_state(self, force=(typ == CMD_GAME_END))

    # -- 予告演出の抽選 -----------------------------------------------------
    def _on_flag(self, flag: str) -> None:
        if self.state != ST_NORMAL or flag not in RARE:
            if self.state == ST_NORMAL and self.rng.random() < 0.04:
                self.emit("banner", rank="白", trigger=flag)
            return

        # レア役の強さ × 自前ヒートで予告ランクを決める
        base = {"弱チェリー": 0, "スイカ": 1, "強チェリー": 2, "チャンス目": 2}[flag]
        level = base + (1 if self.heat >= 3 else 0)
        weights = [
            [40, 30, 20, 8, 2],
            [20, 30, 30, 16, 4],
            [8, 22, 32, 30, 8],
            [3, 12, 25, 45, 15],
        ][min(level, 3)]
        rank = self.rng.choices(BANNER_RANK, weights=weights)[0]
        self.emit("banner", rank=rank, trigger=flag, heat=self.heat)
        self.heat = min(self.heat + 3, 9)


# ---------------------------------------------------------------------------
# 7. 演出送信ブリッジ（副制御 → オーバーレイ）
#
#    副制御の出力先をWebSocket送信に差し替える。外部依存なし（標準ライブラリのみ）。
#    こちらも単方向：クライアントからの受信フレームは破棄し、遊技には一切影響しない。
# ---------------------------------------------------------------------------

WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
WAIT_TIME = 4.1     # 主制御のウェイト。遊技間隔は4.1秒以上（規則）


class EnshutsuServer:
    """演出イベントをWebSocketで配信する最小サーバ。OBSブラウザソースから接続する。"""

    def __init__(self, host: str = "127.0.0.1", port: int = 8765):
        self.host, self.port = host, port
        self.clients: list = []
        self.lock = threading.Lock()
        self.sock: socket.socket | None = None
        self.running = False

    def start(self) -> None:
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((self.host, self.port))
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
# 8. コンパネ通信（主制御 → 中継サーバー → main_control.html）
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
        elif typ in (CMD_GAME_START, CMD_STATE, CMD_POWER_ON):
            note = STATE_NAME.get(data, "")
        elif typ == CMD_NAVI:
            note = "ナビなし" if data == 0xFF else f"押し順{data + 1}"
        self.ws.send({"action": "mainBoard", "type": "cmd", "cmd": f"0x{cmd:04X}",
                      "name": CMD_NAME.get(typ, "?"), "data": data, "note": note})

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
            "diff": r["diff"],
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
        """副制御が受信内容だけから組み立てた状態。主制御の実値とは別物であり、
        ヒートと推測確率状態はあくまで副制御の見立てとして扱う。

        1コマンドごとに呼ばれるが、中継サーバーは受信を全件ログ出力するため、
        中身が前回と変わったときだけ送る（rx と game は比較対象から外す）。
        ただし force のときは変化が無くても送る。通常時が無風だと状態が何分も
        動かず、コンパネ側の生存監視が受信途絶と誤判定するため。"""
        snap = {
            "heat": s.heat,
            "guess": "超高確" if s.heat >= 6 else "高確" if s.heat >= 3 else "低確",
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
# 9. 実行モード
# ---------------------------------------------------------------------------

def run_trace(board: MainBoard, games: int, interval: float = 0.0) -> None:
    print(f"{'G':>5} {'状態':<4} {'成立役':<8} {'表示役':<8} {'払出':>4} {'差枚':>7}  通知")
    print("-" * 68)
    board.power_on()
    try:
        for _ in range(games):
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
        elif typ in (CMD_GAME_START, CMD_STATE):
            note = STATE_NAME.get(data, "")
        elif typ == CMD_NAVI:
            note = "ナビなし" if data == 0xFF else f"押し順{data + 1}"
        print(f"0x{cmd:04X}  {CMD_NAME.get(typ, '?'):<12} {data:>3}  {note}")


def run_events(board: MainBoard, games: int, seed: int | None = None) -> None:
    """副制御が出力する演出イベントをJSON Linesで流す（外部中継用の形）。"""
    board.sub = SubBoard(
        rng=random.Random(seed),
        on_event=lambda ev: print(json.dumps(ev, ensure_ascii=False), flush=True),
    )
    board.power_on()
    try:
        for _ in range(games):
            board.play()
    except BrokenPipeError:
        pass


def drain_inject(board: MainBoard, srv: "EnshutsuServer") -> None:
    """コンパネから届いた注入フレームを遊技スレッド側で流し込む。

    main2sub … 主制御の送信口をそのまま使う。副制御は正規の受信と区別できず、
               コマンド生ログにも同じ形で残る。副制御のロジック検証用。
    enshutsu … 副制御の判断を飛ばしてオーバーレイへ直送する。表示確認用。
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
        except (TypeError, ValueError):
            pass


def run_live(board: MainBoard, games: int, host: str, port: int,
             interval: float, seed: int | None = None) -> None:
    """演出イベントをWebSocketで配信しながら稼働させる。"""
    srv = EnshutsuServer(host, port)
    srv.start()
    print(f"演出配信中: ws://{host}:{port}  （Ctrl+Cで停止）", file=sys.stderr)
    board.sub = SubBoard(rng=random.Random(seed), on_event=srv.broadcast,
                         panel=board.panel)
    board.power_on()
    try:
        for _ in range(games):
            board.play()
            # ウェイト中も注入を拾えるよう、細かく刻んで待つ
            deadline = time.monotonic() + max(interval, 0.0)
            while True:
                drain_inject(board, srv)
                if time.monotonic() >= deadline:
                    break
                time.sleep(0.05)
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


def run_sim(setting: int, machines: int, games: int) -> None:
    diffs = []
    for i in range(machines):
        b = MainBoard(setting=setting, rng=random.Random(i))
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


def main() -> None:
    ap = argparse.ArgumentParser(description="GODタイプ主制御シミュレータ")
    ap.add_argument("--setting", type=int, default=1, choices=range(1, 7))
    ap.add_argument("--games", type=int, default=10000)
    ap.add_argument("--trace", type=int, default=0, help="1G毎のログをNゲーム分表示")
    ap.add_argument("--sim", type=int, default=0, help="N台分の分布を集計")
    ap.add_argument("--commands", type=int, default=0, help="主→副コマンドをNゲーム分表示")
    ap.add_argument("--events", type=int, default=0, help="副制御の演出イベントをJSONで出力")
    ap.add_argument("--serve", action="store_true", help="演出イベントをWebSocketで配信")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--interval", type=float, default=WAIT_TIME,
                    help=f"1ゲームの間隔（秒）。既定はウェイト{WAIT_TIME}秒")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--panel", default=PANEL_URL,
                    help=f"コンパネ中継サーバー(trigger_relay_server.js)のURL。既定 {PANEL_URL}")
    ap.add_argument("--no-panel", action="store_true", help="コンパネへの送信を行わない")
    ap.add_argument("--panel-cmds", action="store_true",
                    help="主→副の2バイトコマンド生ログもコンパネへ送る")
    a = ap.parse_args()

    if a.sim:
        run_sim(a.setting, a.sim, a.games)
        return
    board = MainBoard(setting=a.setting, rng=random.Random(a.seed))
    # --serve / --trace のときだけコンパネへ送る（集計モードでは送らない）
    if not a.no_panel and (a.serve or a.trace):
        board.panel = PanelLink(a.panel, raw_cmds=a.panel_cmds)
    try:
        if a.serve:
            run_live(board, a.games, a.host, a.port, a.interval, a.seed)
        elif a.commands:
            run_commands(board, a.commands)
        elif a.events:
            run_events(board, a.events, a.seed)
        elif a.trace:
            run_trace(board, a.trace, interval=0.0)
        else:
            run_single(board, a.games)
    finally:
        if board.panel:
            board.panel.send_summary(board)
            board.panel.close()


if __name__ == "__main__":
    main()
