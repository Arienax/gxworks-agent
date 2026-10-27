@echo off
rem Copyright 2026 Arienax
rem Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.
title GXWorks Agent Web
powershell.exe -NoProfile -STA -ExecutionPolicy Bypass -File "%~dp0scripts\start_web.ps1"
if errorlevel 1 pause
