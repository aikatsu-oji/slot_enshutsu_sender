@echo off
chcp 932 >nul
title 演出トリガー中継サーバー
cd /d "%~dp0"
echo 中継サーバーを起動します...
echo.
node trigger_relay_server.js
echo.
echo [情報] サーバーが停止しました(上にエラーメッセージが出ていないか確認してください)。
pause
