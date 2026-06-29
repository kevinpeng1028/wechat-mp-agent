"""配置加载器 - 支持环境变量替换和 YAML 配置"""

import os
import re
from pathlib import Path
from typing import Any, Optional

try:
    import yaml
except ImportError:
    raise ImportError("请先安装依赖: pip install -r requirements.txt")

from dotenv import load_dotenv

# 项目根目录
PROJECT_ROOT = Path(__file__).parent.parent

# 环境变量文件
_ENV_FILE = PROJECT_ROOT / ".env"


def _resolve_env_vars(value: str) -> str:
    """递归解析 ${VAR} 格式的环境变量引用，支持嵌套。"""
    pattern = re.compile(r'\$\{([^}]+)\}')

    def replacer(match):
        var_name = match.group(1)
        env_val = os.environ.get(var_name, "")
        if env_val == "":
            return match.group(0)  # 保留未解析的引用
        # 递归解析（处理值中包含其他 ${VAR} 的情况）
        return pattern.sub(replacer, env_val)

    if isinstance(value, str):
        resolved = pattern.sub(replacer, value)
        return resolved
    return value


def _deep_resolve(obj: Any) -> Any:
    """深度遍历字典/列表，解析所有字符串中的环境变量。"""
    if isinstance(obj, dict):
        return {k: _deep_resolve(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_deep_resolve(item) for item in obj]
    elif isinstance(obj, str):
        return _resolve_env_vars(obj)
    return obj


class Config:
    """全局单例配置管理器"""

    _instance: Optional["Config"] = None
    _data: dict = {}

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if self._data:  # 避免重复加载
            return

        # 加载 .env 文件
        if _ENV_FILE.exists():
            load_dotenv(_ENV_FILE, override=True)
        else:
            load_dotenv(PROJECT_ROOT / ".env.example", override=False)

        # 加载 YAML 配置
        config_path = PROJECT_ROOT / "config" / "config.yaml"
        if config_path.exists():
            with open(config_path, "r", encoding="utf-8") as f:
                raw_config = yaml.safe_load(f)
                self._data = _deep_resolve(raw_config or {})
        else:
            raise FileNotFoundError(f"配置文件不存在: {config_path}")

    @property
    def data(self) -> dict:
        return self._data

    def get(self, key: str, default: Any = None) -> Any:
        """支持点号路径访问嵌套配置, 如 'llm.model'"""
        keys = key.split(".")
        val = self._data
        for k in keys:
            if isinstance(val, dict) and k in val:
                val = val[k]
            else:
                return default
        return val

    @property
    def project_root(self) -> Path:
        return PROJECT_ROOT

    @property
    def data_dir(self) -> Path:
        d = PROJECT_ROOT / "data"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def reload(self):
        """重新加载配置（用于热更新）"""
        self._data.clear()
        self.__init__()
