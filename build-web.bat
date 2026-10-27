@echo off
rem Copyright 2026 Arienax
rem Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.
setlocal EnableExtensions
chcp 65001 >nul 2>&1
cd /d "%~dp0"
title GXWorks Agent - Build Web Source Runtime

set "NO_PAUSE="
set "FRONTEND_ONLY="

:parse_args
if "%~1"=="" goto :args_done
if /I "%~1"=="--no-pause" set "NO_PAUSE=1"
if /I "%~1"=="--frontend-only" set "FRONTEND_ONLY=1"
shift
goto :parse_args

:args_done
set "PS_ARGS="
if defined FRONTEND_ONLY set "PS_ARGS=-FrontendOnly"

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\build_web_source.ps1" %PS_ARGS%
set "BUILD_EXIT=%ERRORLEVEL%"

if not "%BUILD_EXIT%"=="0" (
    echo.
    echo [ERROR] Web source build failed with exit code %BUILD_EXIT%.
    echo [INFO] See build-web.log in this directory for details.
)
if not defined NO_PAUSE pause
endlocal & exit /b %BUILD_EXIT%
