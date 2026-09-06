@echo off
setlocal
rem ============================================================
rem  旧配置 -> 新配置への一回限りの移行スクリプト
rem    god_main_board.py      -> main_board\
rem    main_control.html      -> control\
rem    *.pdf / *.docx (ルート・enshutsu) -> doc\
rem  git 管理下なら git mv、そうでなければ move を使う。二重実行しても安全。
rem  実行後は git status で確認し、コミットしてください。
rem  あわせて scripts\config の設定を .claude\settings.json / .vscode\tasks.json に配置します(未存在時のみ)。
rem ============================================================
cd /d "%~dp0.."
echo [情報] 作業フォルダ: %cd%
echo.

set "USEGIT=0"
where git >nul 2>nul && if exist ".git" set "USEGIT=1"
if "%USEGIT%"=="1" (echo [情報] git mv で移動します) else (echo [情報] git が無いため move で移動します)
echo.

if not exist "main_board" mkdir "main_board"
if not exist "control"    mkdir "control"
if not exist "doc"        mkdir "doc"

call :MV "god_main_board.py"                     "main_board\god_main_board.py"
call :MV "main_control.html"                     "control\main_control.html"
call :MV "スロットの概念.pdf"                    "doc\スロットの概念.pdf"
call :MV "enshutsu\主制御・副制御仕様書.docx"    "doc\主制御・副制御仕様書.docx"

echo.
echo [情報] Claude Code / VS Code の設定ファイルを配置します...
if not exist ".claude" mkdir ".claude"
if not exist ".vscode" mkdir ".vscode"
if exist ".claude\settings.json" (echo [済み] .claude\settings.json) else (copy /y "scripts\config\claude.settings.json" ".claude\settings.json" >nul && echo [配置] .claude\settings.json)
if exist ".vscode\tasks.json"    (echo [済み] .vscode\tasks.json)    else (copy /y "scripts\config\vscode.tasks.json" ".vscode\tasks.json" >nul && echo [配置] .vscode\tasks.json)

if "%USEGIT%"=="1" (
  echo.
  echo [情報] 新規/更新ファイルをステージします...
  git add -A -- .gitignore README.md CLAUDE.md package.json package-lock.json setup.bat server scripts .claude .vscode main_board control doc 2>nul
  echo.
  git status --short
)

echo.
echo [完了] 移行が終わりました。内容を確認してコミットしてください:
echo         git commit -m "フォルダ構成を整理 (main_board/ control/ doc/ scripts/)"
echo.
pause
exit /b 0

:MV
if not exist "%~1" (
  if exist "%~2" (echo [済み] %~2) else (echo [注意] %~1 が見つかりません。スキップします)
  exit /b 0
)
if exist "%~2" (
  echo [注意] 移動先 %~2 が既に存在します。%~1 はそのまま残します
  exit /b 0
)
if "%USEGIT%"=="1" (
  git ls-files --error-unmatch "%~1" >nul 2>nul
  if not errorlevel 1 (
    git mv "%~1" "%~2" && echo [移動] %~1 -^> %~2 ^(git mv^)
    exit /b 0
  )
)
move /y "%~1" "%~2" >nul && echo [移動] %~1 -^> %~2
exit /b 0
