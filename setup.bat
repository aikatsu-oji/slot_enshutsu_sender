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

echo [1/6] Node.jsを確認しています...
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
echo [2/6] 必要なパッケージ(ws)を確認しています...
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
echo [3/6] 必要なファイルを確認しています...
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
echo [4/6] 中継サーバーの状態を確認しています...
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
echo [5/6] main_control と enshutsu_overlay(と筐体ビュー kyotai)をボーダレスウィンドウで開きます...

rem 筐体ビュー(kyotai.html)の場所を探す。kyotai\ / enshutsu\ / ルート のどこに置いてもよい。
rem 見つかった場所をそのままURLに使うので、移動しても以降の書き換えは不要。
set "KYOTAI_URL="
if exist "kyotai\kyotai.html" set "KYOTAI_URL=kyotai/kyotai.html"
if not defined KYOTAI_URL if exist "enshutsu\kyotai.html" set "KYOTAI_URL=enshutsu/kyotai.html"
if not defined KYOTAI_URL if exist "kyotai.html" set "KYOTAI_URL=kyotai.html"

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
goto CHECK_KYOTAI

:SKIP_ENSHUTSU
echo [警告] enshutsu\enshutsu_overlay.html が見つかりません。enshutsuフォルダ内に配置してください。

:CHECK_KYOTAI
rem 筐体ビュー(kyotai.html)。主制御と連動モード・操作パネル非表示で開く。任意ファイルなので無くても続行。
if defined KYOTAI_URL goto OPEN_KYOTAI
echo [情報] kyotai.html が見つからないため、筐体ビューはスキップします。
echo         kyotai\ / enshutsu\ / ルート のいずれかに置くと自動で開きます。
goto MAIN_BOARD

:OPEN_KYOTAI
echo [情報] 筐体ビュー: %KYOTAI_URL%
start "" "%BROWSER_PATH%" --new-window --app="%BASEURL%/%KYOTAI_URL%?mode=link&hidebar=1" --window-size=420,980 --window-position=1490,0
goto MAIN_BOARD

:NO_BROWSER
echo [警告] Chrome/Edgeが見つからなかったため、通常のブラウザウィンドウで開きます。
if exist "main_control.html" start "" "%BASEURL%/main_control.html"
if exist "enshutsu\enshutsu_overlay.html" start "" "%BASEURL%/enshutsu/enshutsu_overlay.html"
if defined KYOTAI_URL start "" "%BASEURL%/%KYOTAI_URL%?mode=link&hidebar=1"

:MAIN_BOARD
echo.
echo [6/6] 主制御(god_main_board.py)を起動します...

rem 主制御スクリプトの場所。ルート / main_board / server のいずれかに置く。
set "MB="
if exist "god_main_board.py" set "MB=god_main_board.py"
if not defined MB if exist "main_board\god_main_board.py" set "MB=main_board\god_main_board.py"
if not defined MB if exist "server\god_main_board.py" set "MB=server\god_main_board.py"
if not defined MB goto NO_MAIN_BOARD

rem Python 3 を探す(py ランチャー優先)。
set "PY="
py -3 --version >nul 2>nul
if not errorlevel 1 set "PY=py -3"
if defined PY goto PY_FOUND
python --version >nul 2>nul
if not errorlevel 1 set "PY=python"
if not defined PY goto NO_PYTHON
:PY_FOUND
for /f "tokens=*" %%V in ('%PY% --version 2^>^&1') do echo [OK] %%V を検出しました。(%PY%)
echo [情報] 主制御スクリプト: %MB%

rem 通信なしで2000Gだけ回して、スクリプト単体が動くことを確認する。
echo [確認] 主制御の単体テストを実行しています(2000G / コンパネ送信なし)...
%PY% "%MB%" --games 2000 --seed 1 --no-panel
if errorlevel 1 goto MAIN_BOARD_FAIL
echo [OK] 主制御の単体テストに成功しました。

rem 副制御は主制御の2バイトコマンドを受けて演出イベントを組み立てる。通信なしで
rem 空回しし、副制御側だけが原因で落ちていないかを先に切り分ける。
echo [確認] 副制御の単体テストを実行しています(300イベント / 通信なし)...
%PY% "%MB%" --events 300 --seed 1 --no-panel >nul
if errorlevel 1 goto SUB_BOARD_FAIL
echo [OK] 副制御の単体テストに成功しました。
echo.

echo   起動モードを選んでください。
echo   いずれも --serve なので、主制御モニタと副制御モニタの両方が動きます。
echo   主→副の2バイトコマンド生ログも常に送ります。
echo     [1] 通常        実機ウェイト(4.1秒/G)で稼働。設定1
echo     [2] 高速テスト  0.5秒/G。契機が早く出るよう設定6
echo     [3] 天井テスト  seed固定。1200G目で天井到達(0.2秒/G・約4分)
echo     [4] 起動しない  (あとで手動: %PY% %MB% --serve)
echo.
choice /c 1234 /n /m "  番号を入力: "
set "MB_SEL=%errorlevel%"

set "MB_ARGS="
if "%MB_SEL%"=="1" set "MB_ARGS=--serve --setting 1 --games 100000"
if "%MB_SEL%"=="2" set "MB_ARGS=--serve --setting 6 --interval 0.5 --games 100000"
if "%MB_SEL%"=="3" set "MB_ARGS=--serve --setting 1 --interval 0.2 --games 3000 --seed 6"
if not defined MB_ARGS goto MAIN_BOARD_SKIPPED

rem 主副間のやり取りを1コマンド単位で追うため、コマンド生ログは常に送る。
set "MB_ARGS=%MB_ARGS% --panel-cmds"

rem 副制御 → ws://127.0.0.1:8765(オーバーレイ) / 主制御モニタ端子 → ws://127.0.0.1:%PORT%(コンパネ)
echo [起動] %PY% %MB% %MB_ARGS%
start "主制御 god_main_board.py" cmd /k %PY% "%MB%" %MB_ARGS% --panel ws://127.0.0.1:%PORT%
echo [情報] 停止は主制御ウィンドウで Ctrl+C。
echo         main_control の「主制御モニタ」と「副制御モニタ」が両方「受信中」になれば経路OKです。
goto DONE

:NO_MAIN_BOARD
echo [情報] god_main_board.py が見つからないため、主制御の起動はスキップします。
echo         ルート / main_board\ / server\ のいずれかに置くと自動で起動対象になります。
goto DONE

:NO_PYTHON
echo [警告] Python 3 が見つからないため、主制御の起動はスキップします。
echo         https://www.python.org/ からインストールし、"Add python.exe to PATH" にチェックを入れてください。
goto DONE

:MAIN_BOARD_FAIL
echo [警告] 主制御スクリプトの単体テストに失敗しました。上のエラー内容を確認してください。
echo         主制御の起動はスキップします。
goto DONE

:SUB_BOARD_FAIL
echo [警告] 副制御(SubBoard)の単体テストに失敗しました。上のエラー内容を確認してください。
echo         god_main_board.py 第6節の SubBoard を確認してください。主制御の起動はスキップします。
goto DONE

:MAIN_BOARD_SKIPPED
echo [情報] 主制御は起動しません。必要になったら次を実行してください:
echo         %PY% %MB% --serve

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
echo  主制御を起動した場合は、そのウィンドウも閉じないでください(停止は Ctrl+C)。
echo.
echo  main_control には主制御モニタと副制御モニタが並びます。副制御の推測確率状態の
echo  真下に主制御の実値が出るので、食い違ったときは実値側が赤くなります。
echo  同じ画面の「信号注入」から2バイトコマンドや演出イベントを流し込めます。
echo.
echo  筐体ビュー(kyotai.html)は主制御の1G結果を受けてリールが止まります。左上の歯車ボタンでローカル試打に切替可。
if defined KYOTAI_URL echo  筐体ビューのURL: %BASEURL%/%KYOTAI_URL%
echo  ウィンドウのサイズ・位置は端をドラッグして自由に調整してください。
echo ============================================
echo.
pause
