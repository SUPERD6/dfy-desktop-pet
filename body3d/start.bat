@echo off
rem 大肥鱼 3D 身体启动器：优先用仓库里的便携 Node（.tools\node），没有就用系统 node
cd /d "%~dp0"
set "TOOLS=%~dp0..\.tools"
if exist "%TOOLS%\node\node.exe" (
    set "PATH=%TOOLS%\node;%PATH%"
    if not exist "%TOOLS%\tmp" mkdir "%TOOLS%\tmp"
    set "TEMP=%TOOLS%\tmp"
    set "TMP=%TOOLS%\tmp"
    "%TOOLS%\node\node.exe" "%~dp0node_modules\electron\cli.js" . %*
) else (
    node "%~dp0node_modules\electron\cli.js" . %*
)
