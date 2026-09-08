@echo off
rem 主制御を手動で操作するためのメニュー。中身は scripts\dev.cmd の呼び出しだけ。
rem
rem 【文字コードの注意】このファイルは Shift-JIS(cp932) + CRLF。
rem scripts\dev.cmd が呼ぶ dev.ps1 は [Console]::OutputEncoding を UTF-8 に変えるので、
rem 呼び出したあとはこのファイルの日本語が読めなくなり画面が文字化けする。
rem そのため dev.cmd を呼んだ直後は必ず chcp 932 に戻してから日本語を表示する。
chcp 932 >nul
setlocal
title 主制御 手動操作
cd /d "%~dp0"

echo ============================================
echo  主制御 手動操作 (レバーON / クレジット投入)
echo ============================================
echo.
echo 中継サーバーと主制御(手動モード)を起動します。
echo すでに起動していればそのまま使います(手動モードに切り替えます)。
echo.
call scripts\dev.cmd start -Mode manual
if errorlevel 1 goto FAIL
call scripts\dev.cmd send manual >nul 2>nul
chcp 932 >nul
echo.
echo 手動モードでは主制御は自分から回りません。[1] のレバーONで1ゲームずつ進みます。
echo ウェイト中(前回の回転開始から4.1秒以内)のレバーONは実機と同じく効きません。
echo ベットはMAXベット(3枚)のみ。クレジットが足りないと回らないので [2] で足してください。
echo 遊技の結果はコンパネ(主制御モニタ)か [6] のログで確認できます。

:MENU
chcp 932 >nul
echo.
echo --------------------------------------------
echo  [1] レバーON (1ゲーム進める)
echo  [2] クレジット投入 +50枚
echo  [3] 自動で回す (auto)
echo  [4] 手動に戻す (manual)
echo  [5] 状態を表示
echo  [6] ログの末尾を表示
echo  [7] コンパネ / オーバーレイを開く
echo  [0] 主制御と中継サーバーを停止して終了
echo  [Q] 起動したまま終了
echo --------------------------------------------
choice /c 12345670Q /n /m "操作を選んでください: "
set "K=%errorlevel%"
if "%K%"=="1" call scripts\dev.cmd send lever
if "%K%"=="2" call scripts\dev.cmd send credit
if "%K%"=="3" call scripts\dev.cmd send auto
if "%K%"=="4" call scripts\dev.cmd send manual
if "%K%"=="5" call scripts\dev.cmd status
if "%K%"=="6" call scripts\dev.cmd logs
if "%K%"=="7" call scripts\dev.cmd open
if "%K%"=="8" goto STOP
if "%K%"=="9" goto LEAVE
goto MENU

:STOP
echo.
call scripts\dev.cmd stop
chcp 932 >nul
goto END

:LEAVE
chcp 932 >nul
echo.
echo 主制御と中継サーバーは起動したままです。止めるときは scripts\dev.cmd stop を実行してください。
goto END

:FAIL
chcp 932 >nul
echo.
echo [エラー] 起動に失敗しました。上のメッセージを確認してください。
pause
endlocal
exit /b 1

:END
endlocal
exit /b 0
