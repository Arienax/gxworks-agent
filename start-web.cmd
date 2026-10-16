@echo off
title GXWorks Agent Web
powershell.exe -NoProfile -STA -ExecutionPolicy Bypass -File "%~dp0scripts\start_web.ps1"
if errorlevel 1 pause
