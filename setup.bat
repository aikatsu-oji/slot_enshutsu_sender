@echo off
setlocal enabledelayedexpansion

echo ============================================
echo  演出トリガー中継サーバー 環境構築 / 起動
echo ============================================
echo.

cd /d "%~dp0"
echo [情報] 作業フォルダ: %cd%
echo.

echo [1/5] Node.jsを確認しています...
where node >nul 2>nul
if errorlevel 1 goto NO_NODE
echo [OK] Node.js を検出しました。
node -v
echo.
goto CHECK_WS

:NO_NODE
echo [エラー] Node.js が見つかりません。
echo https://nodejs.org/ からインストールしてから、もう一度このファイルを実行してください。
echo.
pause
exit /b 1

:CHECK_WS
echo [2/5] 必要なパッケージ(ws)を確認しています...
if exist "node_modules\ws" goto WS_OK
echo [セットアップ] 初回のみ: ws パッケージをインストールします。少し時間がかかります...
call npm install ws
if errorlevel 1 goto NPM_FAIL
echo [OK] インストールが完了しました。
goto CHECK_FILES

:NPM_FAIL
echo [エラー] npm install に失敗しました。上のメッセージを確認してください。
echo よくある原因: インターネット接続がない/プロキシ設定/フォルダの書き込み権限がない
echo.
pause
exit /b 1

:WS_OK
echo [OK] ws パッケージは導入済みです。

:CHECK_FILES
echo.
echo [3/5] 必要なファイルを確認しています...
if not exist "server\trigger_relay_server.js" goto MISSING_FILE
if not exist "run_server.bat" goto MISSING_FILE
echo [OK] 必要なファイルが揃っています。
goto CHECK_PORT

:MISSING_FILE
echo [エラー] server\trigger_relay_server.js または run_server.bat が見つかりません。
echo trigger_relay_server.js は server フォルダ内に、run_server.bat はこのバッチファイルと同じフォルダに配置してください。
echo.
pause
exit /b 1

:CHECK_PORT
echo.
echo [4/5] 中継サーバーの状態を確認しています...
set "PORT_IN_USE=0"
netstat -ano | findstr ":8787" | findstr "LISTENING" >nul 2>nul
if "%errorlevel%"=="0" set "PORT_IN_USE=1"

if "%PORT_IN_USE%"=="1" goto SERVER_ALREADY_RUNNING
echo [起動] 中継サーバーを別ウィンドウで起動します...
start "演出トリガー中継サーバー" "%~dp0run_server.bat"
timeout /t 2 /nobreak >nul
goto OPEN_WINDOWS

:SERVER_ALREADY_RUNNING
echo [情報] 中継サーバーはすでに起動しているようです。ポート8787は使用中です。
echo 新しいサーバーは起動せず、操作パネルのみ開きます。

:OPEN_WINDOWS
echo.
echo [5/5] main_control と enshutsu_overlay をボーダレスウィンドウで開きます...

set "DIRPATH=%~dp0"
set "DIRURL=%DIRPATH:\=/%"

set "BROWSER_PATH="
if exist "%ProgramFiles%\Google\Chrome\Application\chrome.exe" set "BROWSER_PATH=%ProgramFiles%\Google\Chrome\Application\chrome.exe"
if not defined BROWSER_PATH if exist "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe" set "BROWSER_PATH=%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"
if not defined BROWSER_PATH if exist "%LocalAppData%\Google\Chrome\Application\chrome.exe" set "BROWSER_PATH=%LocalAppData%\Google\Chrome\Application\chrome.exe"
if not defined BROWSER_PATH if exist "%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe" set "BROWSER_PATH=%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"
if not defined BROWSER_PATH if exist "%ProgramFiles%\Microsoft\Edge\Application\msedge.exe" set "BROWSER_PATH=%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"

if not defined BROWSER_PATH goto NO_BROWSER

if not exist "main_control.html" goto SKIP_MAIN_CONTROL
start "" "%BROWSER_PATH%" --new-window --app="file:///%DIRURL%main_control.html" --window-size=480,900 --window-position=0,0
goto CHECK_ENSHUTSU

:SKIP_MAIN_CONTROL
echo [警告] main_control.html が見つかりません。

:CHECK_ENSHUTSU
if not exist "enshutsu\enshutsu_overlay.html" goto SKIP_ENSHUTSU
start "" "%BROWSER_PATH%" --new-window --app="file:///%DIRURL%enshutsu/enshutsu_overlay.html" --window-size=960,540 --window-position=520,0
goto DONE

:SKIP_ENSHUTSU
echo [警告] enshutsu\enshutsu_overlay.html が見つかりません。enshutsuフォルダ内に配置してください。
goto DONE

:NO_BROWSER
echo [警告] Chrome/Edgeが見つからなかったため、通常のブラウザウィンドウで開きます。
if exist "main_control.html" start "" "main_control.html"
if exist "enshutsu\enshutsu_overlay.html" start "" "enshutsu\enshutsu_overlay.html"

:DONE
echo.
echo ============================================
echo  準備完了です。
echo  中継サーバーのウィンドウは閉じずに起動したままにしてください。
echo  ウィンドウのサイズ・位置は端をドラッグして自由に調整してください。
echo ============================================
echo.
pause
