@echo off
REM ============================================================================
REM  R5 构建脚本：将后端（FastAPI + Paramiko + ...）打包为独立 exe
REM  产物：backend\dist\devcenter-backend.exe（自包含 Python，目标机无需安装 Python）
REM  前置：本机已安装 Python 3.11+ 且在 PATH 中
REM  用法：双击本文件，或在项目根目录执行 build_backend.bat
REM ============================================================================
cd /d %~dp0..

if not exist backend\build_venv\Scripts\python.exe (
  echo [build] 创建构建用虚拟环境 backend\build_venv ...
  python -m venv backend\build_venv
)

echo [build] 安装依赖（requirements + pyinstaller）...
call backend\build_venv\Scripts\python.exe -m pip install -r backend\requirements.txt pyinstaller -q

echo [build] 运行 PyInstaller 打包 devcenter-backend.exe ...
call backend\build_venv\Scripts\python.exe -m PyInstaller ^
  --onefile ^
  --name devcenter-backend ^
  --paths . ^
  --collect-submodules backend ^
  --hidden-import uvicorn ^
  --hidden-import uvicorn.logging ^
  --hidden-import uvicorn.loops.auto ^
  --hidden-import uvicorn.protocols.http.auto ^
  --hidden-import uvicorn.protocols.websockets.auto ^
  --hidden-import uvicorn.lifespan.on ^
  --hidden-import starlette ^
  --hidden-import multipart ^
  --hidden-import paramiko ^
  --hidden-import cryptography ^
  --hidden-import markdown ^
  --hidden-import requests ^
  --distpath backend\dist ^
  --workpath backend\build ^
  --specpath backend\build ^
  backend\app.py

if exist backend\dist\devcenter-backend.exe (
  echo [build] 完成：backend\dist\devcenter-backend.exe
) else (
  echo [build] 失败：未生成 exe，请检查上方报错
  exit /b 1
)
