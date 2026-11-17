@echo off
chcp 65001 >nul
cd /d "%~dp0"
python rtplayer.py %*
if "%~1"=="" pause
