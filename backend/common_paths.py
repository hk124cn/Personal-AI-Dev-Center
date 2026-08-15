"""
Dev Center - 共享路径解析（R5: PyInstaller 打包后路径感知）

打包前（dev / python 直接运行）和打包后（PyInstaller one-file exe）下，
__file__ 与 cwd 都不再可靠。统一用环境变量解析关键路径，保证两种模式行为一致：

- DEV_CENTER_PACKAGED=1        标记当前运行于打包后的 exe
- DEV_CENTER_RESOURCE_DIR      资源目录（index.html / config.example.json / templates 所在）
                                默认：dev=仓库根；打包=Electron 注入的 resources 目录
- DEV_CENTER_DATA_DIR          可写数据目录（latest.json 落盘处）
                                默认：RESOURCE_DIR/backend/data
                                推荐：Electron 注入 %APPDATA%/Personal AI Dev Center/data（NSIS 安装后 resources 只读）

config.json 始终落在用户数据目录（%APPDATA%/Personal AI Dev Center/），与是否打包无关，
保证升级/重装后配置不丢。
"""

import json
import os
import sys
from pathlib import Path


PACKAGED = os.environ.get("DEV_CENTER_PACKAGED") == "1"


def get_user_data_dir():
    """用户数据目录：配置 / 知识库 / latest.json 持久化处。与平台/打包无关。"""
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / "Personal AI Dev Center"
    elif sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Personal AI Dev Center"
    else:
        return Path.home() / ".config" / "Personal AI Dev Center"


def _resource_dir() -> Path:
    env = os.environ.get("DEV_CENTER_RESOURCE_DIR")
    if env:
        return Path(env)
    if getattr(sys, "frozen", False):
        # PyInstaller one-file：资源随 Electron 放在 resources 目录（由 Electron 注入），
        # 兜底用 _MEIPASS 或 exe 所在目录。
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
        return base
    # dev：本文件位于 backend/common_paths.py -> 仓库根
    return Path(__file__).resolve().parent.parent


def _data_dir() -> Path:
    env = os.environ.get("DEV_CENTER_DATA_DIR")
    if env:
        return Path(env)
    return RESOURCE_DIR / "backend" / "data"


# ---- 用户数据目录（配置持久化，始终可写）----
USER_DATA_DIR = get_user_data_dir()
try:
    USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
except Exception:
    pass
CONFIG_PATH = USER_DATA_DIR / "config.json"

# ---- 资源目录（只读资源：index.html / config.example.json）----
RESOURCE_DIR = _resource_dir()
BASE_DIR = RESOURCE_DIR  # 向后兼容：旧代码引用的仓库根/BASE_DIR

# ---- 数据目录（latest.json 落盘，需可写）----
DATA_DIR = _data_dir()
try:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
except Exception:
    pass
LATEST_JSON = DATA_DIR / "latest.json"

# ---- 默认配置来源（仅用于首次复制，不进包）----
RESOURCE_CONFIG = RESOURCE_DIR / "config.json"
RESOURCE_CONFIG_EXAMPLE = RESOURCE_DIR / "config.example.json"

# ---- 版本号（R5: 打包后由 Electron 经 DEV_CENTER_APP_VERSION 注入，不依赖磁盘 package.json）----
def _app_version() -> str:
    env = os.environ.get("DEV_CENTER_APP_VERSION")
    if env:
        return env
    try:
        with open(RESOURCE_DIR / "package.json", "r", encoding="utf-8") as f:
            return json.load(f).get("version", "1.0.0")
    except Exception:
        return "1.0.0"

APP_VERSION = _app_version()
