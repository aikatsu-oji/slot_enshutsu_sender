@echo off
setlocal

echo ============================================
echo  演出トリガー中継サーバー 環境構築 / 起動
echo ============================================
echo.

cd /d "%~dp0"
echo [情報] 作業フォルダ: %cd%
echo.

echo [1/4] Node.jsを確認しています...
where node >nul 2>nul
if errorlevel 1 (
    echo [エラー] Node.js が見つかりません。
    echo https://nodejs.org/ からインストールしてから、もう一度このファイルを実行してください。
    echo.
    pause
    exit /b 1
)
echo [OK] Node.js を検出しました。
node -v
echo.

echo [2/4] 必要なパッケージ(ws)を確認しています...
if not exist "node_modules\ws" (
    echo [セットアップ] 初回のみ: ws パッケージをインストールします。少し時間がかかります...
    call npm install ws
    if errorlevel 1 (
        echo [エラー] npm install に失敗しました。上のメッセージを確認してください。
        echo よくある原因: インターネット接続がない/プロキシ設定/フォルダの書き込み権限がない
        echo.
        pause
        exit /b 1
    )
    echo [OK] インストールが完了しました。
) else (
    echo [OK] ws パッケージは導入済みです。
)
echo.

echo [3/4] 必要なファイルを確認しています...
if not exist "trigger_relay_server.js" (
    echo [エラー] trigger_relay_server.js が見つかりません。
    echo このバッチファイルと同じフォルダに配置してください。
    echo.
    pause
    exit /b 1
)
if not exist "run_server.bat" (
    echo [エラー] run_server.bat が見つかりません。
    echo このバッチファイルと同じフォルダに配置してください。
    echo.
    pause
    exit /b 1
)
echo [OK] 必要なファイルが揃っています。
echo.

echo [4/4] 中継サーバーの状態を確認しています...
set "PORT_IN_USE=0"
netstat -ano | findstr ":8787" | findstr "LISTENING" >nul 2>nul
if "%errorlevel%"=="0" set "PORT_IN_USE=1"

if "%PORT_IN_USE%"=="1" (
    echo [情報] 中継サーバーはすでに起動しているようです(ポート8787使用中)。
    echo 新しいサーバーは起動せず、操作パネルのみ開きます。
) else (
    echo [起動] 中継サーバーを別ウィンドウで起動します...
    start "演出トリガー中継サーバー" "%~dp0run_server.bat"
    timeout /t 2 /nobreak >nul
)
echo.

if exist "main_control.html" (
    echo [起動] main_control.html を既定のブラウザで開きます...
    start "" "%~dp0main_control.html"
) else (
    echo [警告] main_control.html が見つかりません。同じフォルダに配置してください。
)

echo.
echo ============================================
echo  準備完了です。
echo  中継サーバーのウィンドウは閉じずに起動したままにしてください。
echo  enshutsu_overlay.html は通常通りOBSのブラウザソースとして開いてください。
echo ============================================
echo.
pause
