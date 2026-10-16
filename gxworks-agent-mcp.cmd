@echo off
setlocal
set "ROOT=%~dp0"

if exist "%ROOT%GXWorks-Agent-MCP.exe" (
  "%ROOT%GXWorks-Agent-MCP.exe" %*
  exit /b %ERRORLEVEL%
)

if exist "%ROOT%.venv-mcp\Scripts\python.exe" (
  "%ROOT%.venv-mcp\Scripts\python.exe" "%ROOT%scripts\mcp_entry.py" %*
  exit /b %ERRORLEVEL%
)

if exist "%ROOT%.venv\Scripts\python.exe" (
  "%ROOT%.venv\Scripts\python.exe" "%ROOT%scripts\mcp_entry.py" %*
  exit /b %ERRORLEVEL%
)

where py >nul 2>nul
if %ERRORLEVEL%==0 (
  py -3 "%ROOT%scripts\mcp_entry.py" %*
  exit /b %ERRORLEVEL%
)

where python >nul 2>nul
if %ERRORLEVEL%==0 (
  python "%ROOT%scripts\mcp_entry.py" %*
  exit /b %ERRORLEVEL%
)

echo GXWorks Agent MCP runtime not found. Build/install the MCP runtime or create .venv with requirements/mcp.txt. 1>&2
exit /b 2
