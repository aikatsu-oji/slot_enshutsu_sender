<#
.SYNOPSIS
  演出トリガー中継サーバー / 主制御シミュレータをコマンドラインから操作する開発用スクリプト。
  Claude Code (CLI) や VS Code のターミナルから使うことを想定。人手での起動は従来どおり setup.bat でよい。

.USAGE
  scripts\dev.cmd <command> [options]      (cmd / Git Bash から)
  powershell -ExecutionPolicy Bypass -File scripts\dev.ps1 <command> [options]

  start   [-Mode normal|fast|tenjo|none]   中継サーバーと主制御をバックグラウンド起動(ログは .run\ 配下)
  stop                                     start で起動したプロセスを停止(PIDファイル → ポート の順で探す)
  restart [-Mode ...]                      stop → start
  status                                   ポート/ヘルスチェック/PIDの状態を表示
  test                                     主制御・副制御の単体テスト(通信なし)。setup.bat と同じ内容
  open                                     コンパネ / オーバーレイ / 筐体ビューをアプリウィンドウで開く
  send    <action|json>                    中継サーバーへ1件送る  例: send triggerEnshutsu / send '{"action":"playUpToLock2"}'
  logs    [-Tail 40]                       .run\ 配下のログ末尾を表示
  help

  -Mode は setup.bat の選択肢に対応:
    normal : 実機ウェイト(4.1秒/G) 設定1        (既定)
    fast   : 0.5秒/G 設定6
    tenjo  : seed固定 0.2秒/G 1200Gで天井
    none   : 主制御は起動せず中継サーバーのみ
#>
[CmdletBinding()]
param(
  [Parameter(Position = 0)] [string]$Command = "help",
  [Parameter(Position = 1, ValueFromRemainingArguments = $true)] [string[]]$Rest,
  [ValidateSet("normal", "fast", "tenjo", "none")] [string]$Mode = "normal",
  [int]$Tail = 40
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
# 子プロセス(Python)のログを UTF-8 かつ即時フラッシュにする(.run\main_board.log を読みやすくするため)
$env:PYTHONUTF8 = "1"
$env:PYTHONUNBUFFERED = "1"

$Root      = Split-Path -Parent $PSScriptRoot
$RunDir    = Join-Path $Root ".run"
$Port      = if ($env:PORT) { [int]$env:PORT } else { 8787 }
$SubPort   = 8765
$BaseUrl   = "http://localhost:$Port"
$ServerJs  = Join-Path $Root "server\trigger_relay_server.js"
$MainBoard = Join-Path $Root "main_board\god_main_board.py"
$PidServer = Join-Path $RunDir "server.pid"
$PidBoard  = Join-Path $RunDir "main_board.pid"
$LogServer = Join-Path $RunDir "server.log"
$LogBoard  = Join-Path $RunDir "main_board.log"

New-Item -ItemType Directory -Force -Path $RunDir | Out-Null

function Info($m) { Write-Host "[info] $m" }
function Ok($m)   { Write-Host "[ ok ] $m" -ForegroundColor Green }
function Warn($m) { Write-Host "[warn] $m" -ForegroundColor Yellow }
function Fail($m) { Write-Host "[fail] $m" -ForegroundColor Red; exit 1 }

# 戻り値: @{ Exe = "py"; Pre = @("-3") } のようなハッシュ。見つからなければ $null。
function Get-Python {
  foreach ($c in @(@{ Exe = "py"; Pre = @("-3") }, @{ Exe = "python"; Pre = @() }, @{ Exe = "python3"; Pre = @() })) {
    if (-not (Get-Command $c.Exe -ErrorAction SilentlyContinue)) { continue }
    try {
      $null = & $c.Exe @($c.Pre + @("--version")) 2>&1
      if ($LASTEXITCODE -eq 0) { return $c }
    } catch {}
  }
  return $null
}

function Test-Port([int]$p) {
  $c = New-Object System.Net.Sockets.TcpClient
  try {
    $r = $c.BeginConnect("127.0.0.1", $p, $null, $null)
    if (-not $r.AsyncWaitHandle.WaitOne(300)) { return $false }
    $c.EndConnect($r); return $true
  } catch { return $false } finally { $c.Close() }
}

function Get-PidByPort([int]$p) {
  try {
    $c = Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($c) { return $c.OwningProcess }
  } catch {}
  return $null
}

function Read-Pid($file) {
  if (Test-Path $file) {
    $v = (Get-Content $file -ErrorAction SilentlyContinue | Select-Object -First 1)
    if ($v -match '^\d+$') { return [int]$v }
  }
  return $null
}

function Test-Alive($procId) {
  if (-not $procId) { return $false }
  return [bool](Get-Process -Id $procId -ErrorAction SilentlyContinue)
}

function Stop-ById($procId, $label) {
  if (Test-Alive $procId) {
    # cmd /k 経由などで子プロセスがぶら下がる場合もあるため taskkill /T で木ごと止める
    & taskkill /PID $procId /T /F 2>&1 | Out-Null
    Ok "$label (PID $procId) を停止しました"
  }
}

function Ensure-Deps {
  if (-not (Get-Command node -ErrorAction SilentlyContinue)) { Fail "Node.js が見つかりません (https://nodejs.org/)" }
  if (-not (Test-Path (Join-Path $Root "node_modules\ws"))) {
    Info "ws パッケージをインストールします..."
    Push-Location $Root; try { & npm install --no-audit --no-fund | Out-Host } finally { Pop-Location }
    if (-not (Test-Path (Join-Path $Root "node_modules\ws"))) { Fail "npm install に失敗しました" }
  }
}

function Board-Args($m) {
  switch ($m) {
    "normal" { return @("--serve", "--setting", "1", "--games", "100000") }
    "fast"   { return @("--serve", "--setting", "6", "--interval", "0.5", "--games", "100000") }
    "tenjo"  { return @("--serve", "--setting", "1", "--interval", "0.2", "--games", "3000", "--seed", "6") }
    default  { return $null }
  }
}

function Start-Server {
  if (Test-Port $Port) {
    Warn "ポート $Port は使用中です。中継サーバーはすでに起動しているものとして扱います"
    return
  }
  Ensure-Deps
  if (Test-Path $LogServer) { Remove-Item $LogServer -Force }
  $p = Start-Process -FilePath "node" -ArgumentList @("`"$ServerJs`"") -WorkingDirectory $Root `
        -WindowStyle Hidden -PassThru -RedirectStandardOutput $LogServer -RedirectStandardError (Join-Path $RunDir "server.err.log")
  Set-Content $PidServer $p.Id
  for ($i = 0; $i -lt 20; $i++) { if (Test-Port $Port) { break }; Start-Sleep -Milliseconds 500 }
  if (Test-Port $Port) { Ok "中継サーバー起動 (PID $($p.Id))  $BaseUrl" } else { Fail "中継サーバーが $Port で応答しません。$LogServer を確認してください" }
}

function Start-Board($m) {
  $a = Board-Args $m
  if (-not $a) { Info "主制御は起動しません (-Mode none)"; return }
  if (Test-Port $SubPort) { Warn "ポート $SubPort は使用中です。主制御はすでに起動しているものとして扱います"; return }
  if (-not (Test-Path $MainBoard)) { Fail "主制御スクリプトが見つかりません: $MainBoard  (旧配置のままなら scripts\migrate_layout.bat を一度実行してください)" }
  $py = Get-Python
  if (-not $py) { Fail "Python 3 が見つかりません (https://www.python.org/)" }
  $bargs = @($py.Pre) + @("`"$MainBoard`"") + $a + @("--panel-cmds", "--panel", "ws://127.0.0.1:$Port")
  if (Test-Path $LogBoard) { Remove-Item $LogBoard -Force }
  $p = Start-Process -FilePath $py.Exe -ArgumentList $bargs -WorkingDirectory $Root `
        -WindowStyle Hidden -PassThru -RedirectStandardOutput $LogBoard -RedirectStandardError (Join-Path $RunDir "main_board.err.log")
  Set-Content $PidBoard $p.Id
  for ($i = 0; $i -lt 20; $i++) { if (Test-Port $SubPort) { break }; Start-Sleep -Milliseconds 500 }
  if (Test-Port $SubPort) { Ok "主制御起動 (PID $($p.Id), mode=$m)  ws://127.0.0.1:$SubPort" } else { Warn "主制御が $SubPort で応答しません。$LogBoard を確認してください" }
}

function Do-Stop {
  Stop-ById (Read-Pid $PidBoard)  "主制御"
  Stop-ById (Read-Pid $PidServer) "中継サーバー"
  # PIDファイルに無い(setup.bat 経由など)場合はポートから探す
  foreach ($pair in @(@($SubPort, "主制御(ポート検出)"), @($Port, "中継サーバー(ポート検出)"))) {
    $procId = Get-PidByPort $pair[0]
    if ($procId) { Stop-ById $procId $pair[1] }
  }
  Remove-Item $PidBoard, $PidServer -Force -ErrorAction SilentlyContinue
  Ok "停止処理が完了しました"
}

function Do-Status {
  $s = Test-Port $Port; $b = Test-Port $SubPort
  Write-Host ("中継サーバー  ws/http :{0}  {1}  pid={2}" -f $Port, ($(if ($s) { "LISTENING" } else { "停止" })), (Get-PidByPort $Port))
  Write-Host ("主制御(副制御ポート) :{0}  {1}  pid={2}" -f $SubPort, ($(if ($b) { "LISTENING" } else { "停止" })), (Get-PidByPort $SubPort))
  if ($s) {
    try {
      $h = Invoke-RestMethod "$BaseUrl/api/health" -TimeoutSec 3
      Write-Host ("health: ok  接続クライアント数={0}  root={1}" -f $h.clients, $h.root)
    } catch { Warn "health エンドポイントに応答がありません(古いサーバーが動いている可能性): $($_.Exception.Message)" }
  }
  Write-Host ""
  Write-Host "コンパネ     $BaseUrl/control/main_control.html"
  Write-Host "オーバーレイ $BaseUrl/enshutsu/enshutsu_overlay.html   (OBS ブラウザソース用)"
  Write-Host "筐体ビュー   $BaseUrl/reel/reel.html?mode=link&hidebar=1"
  exit $(if ($s) { 0 } else { 1 })
}

function Do-Test {
  $py = Get-Python
  if (-not $py) { Fail "Python 3 が見つかりません" }
  if (-not (Test-Path $MainBoard)) { Fail "主制御スクリプトが見つかりません: $MainBoard  (旧配置のままなら scripts\migrate_layout.bat を一度実行してください)" }
  $exe = $py.Exe; $pre = @($py.Pre)
  Push-Location $Root
  try {
    Info "主制御 単体テスト (2000G / 通信なし)"
    & $exe @($pre + @($MainBoard, "--games", "2000", "--seed", "1", "--no-panel"))
    if ($LASTEXITCODE -ne 0) { Fail "主制御の単体テストに失敗しました" }
    Info "副制御 単体テスト (300イベント / 通信なし)"
    & $exe @($pre + @($MainBoard, "--events", "300", "--seed", "1", "--no-panel")) | Out-Null
    if ($LASTEXITCODE -ne 0) { Fail "副制御の単体テストに失敗しました" }
    Info "中継サーバー 構文チェック"
    & node --check $ServerJs
    if ($LASTEXITCODE -ne 0) { Fail "trigger_relay_server.js の構文エラー" }
    Info "筐体ビュー 構文チェック (reel/symbols.js, reel/reel_window.js)"
    foreach ($js in @("reel/symbols.js", "reel/reel_window.js")) {
      & node --check (Join-Path $Root $js)
      if ($LASTEXITCODE -ne 0) { Fail "$js の構文エラー" }
    }
    Ok "すべてのテストに成功しました"
  } finally { Pop-Location }
}

function Do-Open {
  if (-not (Test-Port $Port)) { Fail "中継サーバーが起動していません。先に start してください" }
  $browser = @(
    "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
    "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
    "$env:LocalAppData\Google\Chrome\Application\chrome.exe",
    "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
    "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe"
  ) | Where-Object { Test-Path $_ } | Select-Object -First 1
  $pages = @(
    @("$BaseUrl/control/main_control.html", "480,900", "0,0"),
    @("$BaseUrl/enshutsu/enshutsu_overlay.html", "960,576", "520,0"),
    @("$BaseUrl/reel/reel.html?mode=link&hidebar=1", "420,980", "1490,0")
  )
  foreach ($pg in $pages) {
    if ($browser) { Start-Process $browser -ArgumentList @("--new-window", "--app=$($pg[0])", "--window-size=$($pg[1])", "--window-position=$($pg[2])") }
    else { Start-Process $pg[0] }
  }
  Ok "ウィンドウを開きました"
}

function Do-Send($json) {
  if (-not $json) { Fail "送信する内容を指定してください  例: send triggerEnshutsu   /  send '{\"action\":\"playUpToLock2\"}'" }
  # cmd 経由(-File)では引数のダブルクォートが剥がれるため、action 名だけの短縮形も受け付ける
  $json = $json.Trim().Trim("'")
  if ($json -notmatch '^\s*\{') { $json = '{"action":"' + $json + '"}' }
  if (-not (Test-Port $Port)) { Fail "中継サーバーが起動していません" }
  Ensure-Deps
  & node (Join-Path $PSScriptRoot "ws_send.js") "ws://127.0.0.1:$Port" $json
}

function Do-Logs {
  foreach ($f in @($LogServer, $LogBoard, (Join-Path $RunDir "server.err.log"), (Join-Path $RunDir "main_board.err.log"))) {
    if ((Test-Path $f) -and (Get-Item $f).Length -gt 0) {
      Write-Host "===== $(Split-Path -Leaf $f) (末尾 $Tail 行) =====" -ForegroundColor Cyan
      Get-Content $f -Tail $Tail -Encoding UTF8
    }
  }
}

switch ($Command.ToLower()) {
  "start"   { Start-Server; Start-Board $Mode }
  "stop"    { Do-Stop }
  "restart" { Do-Stop; Start-Sleep -Seconds 1; Start-Server; Start-Board $Mode }
  "status"  { Do-Status }
  "test"    { Do-Test }
  "open"    { Do-Open }
  "send"    { Do-Send ($Rest -join " ") }
  "logs"    { Do-Logs }
  default   {
    # 先頭のコメントブロック(<# ... #>)をそのまま表示する
    $text = Get-Content $PSCommandPath -Raw -Encoding UTF8
    if ($text -match '(?s)<#(.*?)#>') { Write-Host $Matches[1].Trim() }
  }
}
