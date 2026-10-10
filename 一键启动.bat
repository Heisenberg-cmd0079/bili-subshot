@echo off
chcp 936 >nul
setlocal
cd /d "%~dp0"
title 字幕长图 - 一键启动

set "VENV=.venv"
set "PY=%VENV%\Scripts\python.exe"
set "PYW=%VENV%\Scripts\pythonw.exe"
set "PIP_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple"
set "UVEXE="

if exist "%PY%" goto run

echo.
echo == 字幕长图 一键启动 ==
echo 只在本项目目录里装 Python 和依赖，不改系统：不写注册表、不改 PATH、不装到 Program Files。
echo.

echo [1/4] 检查 uv
where uv >nul 2>nul
if not errorlevel 1 set "UVEXE=uv"
if defined UVEXE goto haveuv
if exist ".tools\uv.exe" set "UVEXE=.tools\uv.exe"
if defined UVEXE goto haveuv
echo        下载 uv（约 30 MB，来自 github.com/astral-sh/uv）
if not exist ".tools" mkdir ".tools"
curl.exe -L --fail --retry 3 --connect-timeout 20 -o ".tools\uv.zip" "https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip"
if errorlevel 1 goto dlfail
tar.exe -xf ".tools\uv.zip" -C ".tools"
if errorlevel 1 goto dlfail
set "UVEXE=.tools\uv.exe"

:haveuv
echo [2/4] 安装独立 Python 3.12
"%UVEXE%" venv "%VENV%" --python 3.12
if exist "%PY%" goto deps
echo        独立 Python 没装上，改用系统已安装的 Python 重试
"%UVEXE%" venv "%VENV%" --python python
if not exist "%PY%" goto pyfail

:deps
echo [3/4] 安装依赖（OpenCV / Pillow / customtkinter / yt-dlp / imageio-ffmpeg）
"%UVEXE%" pip install --python "%PY%" -r requirements.txt --index-url %PIP_INDEX%
if errorlevel 1 goto pipfail

echo [4/4] 启动界面
:run
start "" "%PYW%" gui_app.pyw
exit /b 0

:dlfail
echo.
echo [失败] 下载 uv 没成功，多半是网络或代理问题。可以自己先装 uv 再运行本脚本：
echo        https://docs.astral.sh/uv/getting-started/installation/
pause
exit /b 1

:pyfail
echo.
echo [失败] 没能建起 Python 环境。请手动安装 Python 3.10 以上（安装时勾选 Add to PATH），再运行本脚本。
pause
exit /b 1

:pipfail
echo.
echo [失败] 依赖没装上。国内网络可以换镜像：用记事本打开本文件，改 PIP_INDEX 那一行。
pause
exit /b 1
