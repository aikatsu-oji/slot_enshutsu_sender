@echo off
setlocal enabledelayedexpansion

echo ============================================
echo  演出トリガー中継サーバー 環境構築 / 起動
echo ============================================
echo.

cd /d "%~dp0"
echo [情報] 作業フォルダ: %cd%

rem 中継サーバーのポート。trigger_relay_server.js の PORT と合わせてください。
set "PORT=8787"
set "BASEURL=http://localhost:%PORT%"
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
netstat -ano | findstr ":%PORT%" | findstr "LISTENING" >nul 2>nul
if "%errorlevel%"=="0" set "PORT_IN_USE=1"

if "%PORT_IN_USE%"=="1" goto SERVER_ALREADY_RUNNING
echo [起動] 中継サーバーを別ウィンドウで起動します...
start "演出トリガー中継サーバー" "%~dp0run_server.bat"
echo [待機] サーバーの応答を待っています...
set "WAIT_COUNT=0"
goto WAIT_SERVER

:WAIT_SERVER
timeout /t 1 /nobreak >nul
netstat -ano | findstr ":%PORT%" | findstr "LISTENING" >nul 2>nul
if "%errorlevel%"=="0" goto SERVER_READY
set /a WAIT_COUNT+=1
if !WAIT_COUNT! GEQ 20 goto SERVER_TIMEOUT
goto WAIT_SERVER

:SERVER_TIMEOUT
echo [警告] 20秒待ちましたが、サーバーの応答を確認できませんでした。
echo         別ウィンドウのエラーメッセージを確認してください。このまま画面を開きます。
goto OPEN_WINDOWS

:SERVER_READY
echo [OK] サーバーが応答しました。
goto OPEN_WINDOWS

:SERVER_ALREADY_RUNNING
echo [情報] 中継サーバーはすでに起動しているようです。ポート%PORT%は使用中です。
echo 新しいサーバーは起動せず、操作パネルのみ開きます。

:OPEN_WINDOWS
echo.
echo [5/5] main_control と enshutsu_overlay をボーダレスウィンドウで開きます...

set "BROWSER_PATH="
if exist "%ProgramFiles%\Google\Chrome\Application\chrome.exe" set "BROWSER_PATH=%ProgramFiles%\Google\Chrome\Application\chrome.exe"
if not defined BROWSER_PATH if exist "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe" set "BROWSER_PATH=%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"
if not defined BROWSER_PATH if exist "%LocalAppData%\Google\Chrome\Application\chrome.exe" set "BROWSER_PATH=%LocalAppData%\Google\Chrome\Application\chrome.exe"
if not defined BROWSER_PATH if exist "%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe" set "BROWSER_PATH=%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"
if not defined BROWSER_PATH if exist "%ProgramFiles%\Microsoft\Edge\Application\msedge.exe" set "BROWSER_PATH=%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"

if not defined BROWSER_PATH goto NO_BROWSER

if not exist "main_control.html" goto SKIP_MAIN_CONTROL
start "" "%BROWSER_PATH%" --new-window --app="%BASEURL%/main_control.html" --window-size=480,900 --window-position=0,0
goto CHECK_ENSHUTSU

:SKIP_MAIN_CONTROL
echo [警告] main_control.html が見つかりません。

:CHECK_ENSHUTSU
if not exist "enshutsu\enshutsu_overlay.html" goto SKIP_ENSHUTSU
start "" "%BROWSER_PATH%" --new-window --app="%BASEURL%/enshutsu/enshutsu_overlay.html" --window-size=960,576 --window-position=520,0
goto DONE

:SKIP_ENSHUTSU
echo [警告] enshutsu\enshutsu_overlay.html が見つかりません。enshutsuフォルダ内に配置してください。
goto DONE

:NO_BROWSER
echo [警告] Chrome/Edgeが見つからなかったため、通常のブラウザウィンドウで開きます。
if exist "main_control.html" start "" "%BASEURL%/main_control.html"
if exist "enshutsu\enshutsu_overlay.html" start "" "%BASEURL%/enshutsu/enshutsu_overlay.html"

:DONE
echo.
echo ============================================
echo  準備完了です。
echo.
echo  OBSのブラウザソースには次のURLを指定してください:
echo    %BASEURL%/enshutsu/enshutsu_overlay.html
echo  ※http経由で開くと cutin / freeze フォルダを自動で読み込むため、
echo    フォルダ選択やアクセス許可のダイアログは一切出ません。
echo.
echo  中継サーバーのウィンドウは閉じずに起動したままにしてください。
echo  ウィンドウのサイズ・位置は端をドラッグして自由に調整してください。
echo ============================================
echo.
pause
