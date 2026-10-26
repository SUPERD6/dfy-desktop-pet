@echo off
rem 大肥鱼桌宠启动器：优先用 pythonw 静默启动（不弹黑框）
cd /d "%~dp0"
if exist "%~dp0app.py" goto run
echo 找不到 app.py，请确认这个 bat 和 app.py 在同一个文件夹里。
pause
exit /b 1
:run
where pythonw.exe >nul 2>nul
if %errorlevel%==0 (
    start "" pythonw.exe "%~dp0app.py"
    exit /b 0
)
where pyw.exe >nul 2>nul
if %errorlevel%==0 (
    start "" pyw.exe -3 "%~dp0app.py"
    exit /b 0
)
if exist "%LOCALAPPDATA%\Programs\Python\Python311\pythonw.exe" (
    start "" "%LOCALAPPDATA%\Programs\Python\Python311\pythonw.exe" "%~dp0app.py"
    exit /b 0
)
echo 没找到 pythonw.exe，请先安装 Python 3，或直接用 python app.py 启动。
pause
