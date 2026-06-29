"""日志系统初始化"""

import sys
from loguru import logger as _logger
from pathlib import Path

LOG_DIR = Path(__file__).parent.parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)


def setup_logger(debug: bool = False):
    level = "DEBUG" if debug else "INFO"

    # 移除默认 handler
    _logger.remove()

    # 控制台输出（带颜色）
    _logger.add(
        sys.stdout,
        format="<green>{time:HH:mm:ss}</green> | <level>{level:<7}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan> - <level>{message}</level>",
        level=level,
        colorize=True,
    )

    # 文件输出（按天轮转）
    _logger.add(
        LOG_DIR / "agent_{time:YYYY-MM-DD}.log",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level:<7} | {name}:{function} - {message}",
        level=level,
        rotation="00:00",
        retention="30 days",
        encoding="utf-8",
    )

    return _logger


logger = setup_logger()
