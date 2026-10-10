@echo off
chcp 936 >nul
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"
title 字幕长图 - 一键启动

REM 测试钩子优先于自动探测：ZIMU_PYTHON 指定系统解释器；
REM ZIMU_NONINTERACTIVE=1 不询问；ZIMU_CHECK_ONLY=1 只检测。
REM 所有 uv 缓存、独立 Python 和下载工具均限制在项目 .tools 内。
set "UV_CACHE_DIR=%~dp0.tools\uv-cache"
set "UV_PYTHON_INSTALL_DIR=%~dp0.tools\python"
set "UV_PYTHON_BIN_DIR=%~dp0.tools\python-bin"
set "UV_NO_CONFIG=1"
set "PIP_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple"
set "VENV_PY=%~dp0.venv\Scripts\python.exe"
set "SYS_PY="
set "SYS_OK=0"
set "SYS_DEPS=0"
set "SYS_FFMPEG=0"
set "VENV_OK=0"
set "VENV_DEPS=0"
set "VENV_FFMPEG=0"
set "PATH_FFMPEG="
set "MODE=auto"
set "CHECK_ONLY=0"
set "NONINTERACTIVE=0"
set "UVEXE="
set "RUN_PY="
set "BRANCH="

:args
if "%~1"=="" goto detect
if /i "%~1"=="--check" goto argcheck
if /i "%~1"=="--project" goto argproject
if /i "%~1"=="--user" goto arguser
if /i "%~1"=="--reuse" goto argreuse
echo [失败] 不认识的参数：%~1
exit /b 2
:argcheck
set "CHECK_ONLY=1"
goto nextarg
:argproject
call :setmode project
goto nextarg
:arguser
call :setmode user
goto nextarg
:argreuse
call :setmode reuse
:nextarg
if "%MODE%"=="invalid" exit /b 2
shift
goto args

:detect
if "%ZIMU_NONINTERACTIVE%"=="1" set "NONINTERACTIVE=1"
if "%ZIMU_CHECK_ONLY%"=="1" set "CHECK_ONLY=1"
echo.
echo == 字幕长图：先检测，后补缺 ==
echo 探测阶段不联网；不修改 PATH，不写系统级 site-packages，不要求管理员权限。

if defined ZIMU_PYTHON goto overridden
for /f "delims=" %%P in ('where.exe python 2^>nul') do call :try_python "%%P"
if defined SYS_PY goto system_found
for /f "delims=" %%P in ('py -3 -B -c "import sys;print(sys.executable)" 2^>nul') do call :try_python "%%P"
goto system_found
:overridden
call :try_python "%ZIMU_PYTHON%"
:system_found
if defined SYS_PY set "SYS_OK=1"
if defined SYS_PY echo [检测] 系统 Python：已有，%SYS_PY%
if not defined SYS_PY echo [检测] 系统 Python：缺，未找到可用的 Python 3.10 以上
if not defined SYS_PY goto probe_ffmpeg
echo [检测] 系统依赖：
call :probe_deps "%SYS_PY%"
if not errorlevel 1 set "SYS_DEPS=1"

:probe_ffmpeg
for /f "delims=" %%F in ('where.exe ffmpeg 2^>nul') do call :try_ffmpeg "%%F"
if defined PATH_FFMPEG echo [检测] PATH ffmpeg：已有，%PATH_FFMPEG%
if not defined PATH_FFMPEG echo [检测] PATH ffmpeg：缺
if not defined SYS_PY goto probe_project
call :probe_ffmpeg "%SYS_PY%"
if not errorlevel 1 set "SYS_FFMPEG=1"

:probe_project
if not exist "%VENV_PY%" goto project_missing
"%VENV_PY%" -B -c "import sys;sys.exit(0 if sys.version_info[:2] >= (3,10) else 1)" >nul 2>nul
if errorlevel 1 goto project_missing
set "VENV_OK=1"
echo [检测] 项目 Python：已有，%VENV_PY%
echo [检测] 项目依赖：
call :probe_deps "%VENV_PY%"
if not errorlevel 1 set "VENV_DEPS=1"
call :probe_ffmpeg "%VENV_PY%"
if not errorlevel 1 set "VENV_FFMPEG=1"
goto decide
:project_missing
echo [检测] 项目环境：缺，.venv 尚不可用

:decide
if "%CHECK_ONLY%"=="1" echo [检测完成] 只检测，不安装，不启动。
if "%CHECK_ONLY%"=="1" exit /b 0
if "%MODE%"=="project" goto project
if "%MODE%"=="user" goto user
if "%SYS_DEPS%%SYS_FFMPEG%"=="11" goto branch_a
if "%VENV_DEPS%%VENV_FFMPEG%"=="11" goto branch_b
if "%MODE%"=="reuse" goto reusefail
if "%SYS_DEPS%"=="1" goto branch_c
if "%SYS_OK%"=="0" goto project
REM 默认 D：非交互或双击不问。终端中只问一次，20 秒默认项目内。
if "%NONINTERACTIVE%"=="1" goto project
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -Command "try { if ([Console]::IsInputRedirected) { exit 1 }; $p=Get-CimInstance Win32_Process -Filter ('ProcessId='+$PID); $c=Get-CimInstance Win32_Process -Filter ('ProcessId='+$p.ParentProcessId); $a=Get-CimInstance Win32_Process -Filter ('ProcessId='+$c.ParentProcessId); if ($a.Name -eq 'explorer.exe') { exit 1 }; exit 0 } catch { exit 1 }" >nul 2>nul
if errorlevel 1 goto project
choice /c 12 /t 20 /d 1 /m "你的系统 Python 缺依赖： [1] 装到项目目录（推荐） [2] 装到你的用户目录"
if errorlevel 2 goto user
goto project

:branch_a
set "BRANCH=A"
set "RUN_PY=%SYS_PY%"
echo [A] 检测到已有可用环境，直接使用，本次没有安装任何东西
echo Python、依赖、ffmpeg 已具备，跳过。
goto run

:branch_b
set "BRANCH=B"
set "RUN_PY=%VENV_PY%"
echo [B] 复用项目内已有环境
echo Python、依赖、ffmpeg 已具备，跳过；本次没有安装任何东西。
goto run

:branch_c
set "BRANCH=C"
set "RUN_PY=%SYS_PY%"
echo [C] Python 和其它依赖已具备，跳过；只补装 ffmpeg。
call :user_ready
if errorlevel 1 goto userfail
"%RUN_PY%" -B -m pip install --user --disable-pip-version-check --no-cache-dir --index-url "%PIP_INDEX%" imageio-ffmpeg
if errorlevel 1 goto installfail
call :probe_ffmpeg "%RUN_PY%"
if errorlevel 1 goto installfail
echo 只补装了 ffmpeg（其它都已具备）
echo ffmpeg 依赖装到：
"%RUN_PY%" -B -c "import site;print(site.getusersitepackages())"
goto run

:user
if "%SYS_OK%"=="0" goto userfail
set "RUN_PY=%SYS_PY%"
if "%SYS_DEPS%%SYS_FFMPEG%"=="11" goto branch_a
call :user_ready
if errorlevel 1 goto userfail
set "BRANCH=D-user"
echo [D] 按你的选择，仅补到用户级目录，不写系统 Python 目录。
"%RUN_PY%" -B -m pip install --user --disable-pip-version-check --no-cache-dir --index-url "%PIP_INDEX%" -r "%~dp0requirements.txt"
if errorlevel 1 goto installfail
echo 用户级依赖装到：
"%RUN_PY%" -B -c "import site;print(site.getusersitepackages())"
goto verify_run

:project
set "RUN_PY=%VENV_PY%"
if "%VENV_DEPS%%VENV_FFMPEG%"=="11" goto branch_b
call :get_uv
if errorlevel 1 goto installfail
if "%VENV_OK%"=="1" goto project_deps
if exist "%~dp0.venv" goto brokenvenv
if "%SYS_OK%"=="1" goto system_venv
set "BRANCH=E"
echo [E] 没有可用 Python，安装项目内独立 Python 3.12。
REM 同时禁止注册表登记和项目外 bin 入口；失败即停止，不静默降级。
"%UVEXE%" python install 3.12 --no-registry --no-bin
if errorlevel 1 goto installfail
"%UVEXE%" venv "%~dp0.venv" --python 3.12 --managed-python --no-python-downloads
if errorlevel 1 goto installfail
goto project_deps
:system_venv
set "BRANCH=D"
echo [D] 系统 Python 已具备，跳过独立 Python 下载。
"%UVEXE%" venv "%~dp0.venv" --python "%SYS_PY%" --no-python-downloads
if errorlevel 1 goto installfail
:project_deps
if not defined BRANCH set "BRANCH=D"
if "%VENV_OK%"=="1" echo 项目 Python 已具备，跳过重建；只补缺的依赖。
"%UVEXE%" pip install --python "%RUN_PY%" -r "%~dp0requirements.txt" --index-url "%PIP_INDEX%"
if errorlevel 1 goto installfail
echo 独立 Python 安装目录：
"%UVEXE%" python dir
echo uv 缓存目录：
"%UVEXE%" cache dir
if "%BRANCH%"=="E" echo 这台机器没有 Python，已在项目目录里装好
if "%BRANCH%"=="D" if "%SYS_DEPS%"=="0" echo 你的系统 Python 缺依赖，已装到项目目录（不动系统）
if "%BRANCH%"=="D" if "%SYS_DEPS%"=="1" echo 按 --project 要求，已在项目目录里准备好环境（不动系统）
goto verify_run

:verify_run
call :probe_deps "%RUN_PY%"
if errorlevel 1 goto installfail
call :probe_ffmpeg "%RUN_PY%"
if errorlevel 1 goto installfail

:run
echo.
echo 解释器：%RUN_PY%
echo 依赖装到 / 已有依赖来自：
"%RUN_PY%" -B -c "import sysconfig,cv2,numpy,PIL,customtkinter,yt_dlp;print('环境 site-packages: '+sysconfig.get_path('purelib'));[print(m.__name__+': '+m.__file__) for m in (cv2,numpy,PIL,customtkinter,yt_dlp)]"
if errorlevel 1 goto installfail
echo [启动] 字幕长图界面
REM 由已验证解释器派生 pythonw 路径，避免系统 python / pythonw 版本错配。
"%RUN_PY%" -B -c "import os,sys,subprocess;w=os.path.join(os.path.dirname(sys.executable),'pythonw.exe');subprocess.Popen([w if os.path.isfile(w) else sys.executable,'-B',os.path.join(os.getcwd(),'gui_app.pyw')],cwd=os.getcwd())"
if errorlevel 1 goto installfail
exit /b 0

:setmode
if not "%MODE%"=="auto" if not "%MODE%"=="%~1" goto badmode
set "MODE=%~1"
exit /b 0
:badmode
echo [失败] --project / --user / --reuse 不能同时指定。
set "MODE=invalid"
exit /b 2

:try_python
if defined SYS_PY exit /b 0
"%~1" -B -c "import sys;sys.exit(0 if sys.version_info[:2] >= (3,10) else 1)" >nul 2>nul
if errorlevel 1 exit /b 1
set "SYS_PY=%~1"
exit /b 0

:probe_deps
"%~1" -B -c "import importlib,sys;mods=('cv2','numpy','PIL','customtkinter','yt_dlp');bad=[];exec('for name in mods:\n try: importlib.import_module(name);print(name+\": 已具备，跳过\")\n except Exception as e: bad.append(name);print(name+\": 缺或不可用 - \"+str(e))');sys.exit(bool(bad))"
exit /b %errorlevel%

:try_ffmpeg
if defined PATH_FFMPEG exit /b 0
"%~1" -version >nul 2>nul
if errorlevel 1 exit /b 1
set "PATH_FFMPEG=%~1"
exit /b 0

:probe_ffmpeg
if defined PATH_FFMPEG echo ffmpeg：已具备，跳过，%PATH_FFMPEG%
if defined PATH_FFMPEG exit /b 0
"%~1" -B -c "import imageio_ffmpeg,os,subprocess;exe=imageio_ffmpeg.get_ffmpeg_exe();subprocess.run([exe,'-version'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=10);print('ffmpeg: 已具备，跳过，'+exe)" 2>nul
if not errorlevel 1 exit /b 0
echo ffmpeg：缺
exit /b 1

:user_ready
REM pip --user 本身禁止写系统目录；拒绝不支持用户 site 的裸 venv。
"%RUN_PY%" -B -c "import site,sys;sys.exit(0 if site.ENABLE_USER_SITE else 1)" >nul 2>nul
if errorlevel 1 exit /b 1
"%RUN_PY%" -B -m pip --version >nul 2>nul
exit /b %errorlevel%

:get_uv
if exist "%~dp0.tools\uv.exe" set "UVEXE=%~dp0.tools\uv.exe"
if defined UVEXE echo 项目 uv 已具备，跳过下载。
if defined UVEXE exit /b 0
for /f "delims=" %%U in ('where.exe uv 2^>nul') do if not defined UVEXE set "UVEXE=%%U"
if defined UVEXE echo uv 已具备，跳过下载。
if defined UVEXE exit /b 0
echo 下载 uv 到项目 .tools，不安装到系统、不改 PATH。
if not exist "%~dp0.tools" mkdir "%~dp0.tools"
"%SystemRoot%\System32\curl.exe" -L --fail --retry 3 --connect-timeout 20 -o "%~dp0.tools\uv.zip" "https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip"
if errorlevel 1 exit /b 1
"%SystemRoot%\System32\tar.exe" -xf "%~dp0.tools\uv.zip" -C "%~dp0.tools"
if errorlevel 1 exit /b 1
if not exist "%~dp0.tools\uv.exe" exit /b 1
set "UVEXE=%~dp0.tools\uv.exe"
exit /b 0

:reusefail
echo [失败] --reuse 只复用完整环境：当前缺 Python、依赖或 ffmpeg，不执行安装。
exit /b 1
:brokenvenv
echo [失败] .venv 已存在但解释器不可用。未删除或覆盖，请先检查该目录。
exit /b 1
:userfail
echo [失败] 当前解释器无法使用 pip --user；未安装任何系统级包。请使用 --project。
exit /b 1
:installfail
echo [失败] 环境准备未完成，已停止，不启动界面。请检查上面的具体错误或镜像连接。
exit /b 1
