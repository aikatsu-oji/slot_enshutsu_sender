@echo off
rem scripts\dev.ps1 ‚ğ cmd / Git Bash / Claude Code ‚©‚çŒÄ‚Ô‚½‚ß‚Ì”–‚¢ƒ‰ƒbƒp[
rem —á: scripts\dev.cmd start -Mode fast   /   scripts\dev.cmd status
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0dev.ps1" %*
exit /b %errorlevel%
