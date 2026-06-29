"""Agent 基础类 - 所有 Agent 的父类"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional


class AgentStatus(Enum):
    IDLE = "idle"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    PARTIAL = "partial"  # 部分完成


@dataclass
class AgentResult:
    """统一的 Agent 执行结果"""
    status: AgentStatus
    agent_name: str
    output: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    duration_seconds: float = 0.0
    timestamp: datetime = field(default_factory=datetime.now)

    @property
    def is_success(self) -> bool:
        return self.status in (AgentStatus.SUCCESS, AgentStatus.PARTIAL)

    def to_dict(self) -> dict:
        return {
            "status": self.status.value,
            "agent_name": self.agent_name,
            "output": self.output,
            "error": self.error,
            "duration_seconds": self.duration_seconds,
            "timestamp": self.timestamp.isoformat(),
        }


class BaseAgent(ABC):
    """所有 Agent 的基类，定义统一的生命周期接口"""

    name: str = "base_agent"
    display_name: str = "Base Agent"

    def __init__(self, config: Optional[Dict] = None):
        from src.config import Config
        self.config = config or Config()
        self._status = AgentStatus.IDLE

    def get_config(self, key: str, default: Any = None) -> Any:
        """支持点号路径访问嵌套配置，兼容 Config 对象和字典"""
        if hasattr(self.config, 'get') and callable(getattr(self.config, 'get')):
            # 如果是 Config 对象，它有 get 方法支持点号
            if hasattr(self.config, 'data'):
                return self.config.get(key, default)
        # 字典：手动解析点号路径
        keys = key.split(".")
        val = self.config
        for k in keys:
            if isinstance(val, dict) and k in val:
                val = val[k]
            else:
                return default
        return val

    @property
    def status(self) -> AgentStatus:
        return self._status

    # ---- 生命周期方法（可被子类覆写）----
    
    def pre_run(self, context: Dict) -> None:
        """执行前检查/准备工作"""
        logger = __import__("src.logger", fromlist=["logger"]).logger
        logger.info(f"[{self.display_name}] 开始准备...")

    def post_run(self, result: AgentResult) -> AgentResult:
        """执行后处理（日志、清理等）"""
        return result

    # ---- 核心接口 ----

    async def run(self, context: Dict) -> AgentResult:
        """完整的执行生命周期：pre_run → execute → post_run"""
        import time as _time
        start = _time.time()
        
        try:
            self.pre_run(context)
            self._status = AgentStatus.RUNNING
            
            result = await self.execute(context)
            
            self._status = result.status or (AgentStatus.SUCCESS if result.is_success else AgentStatus.FAILED)
            result.duration_seconds = round(_time.time() - start, 2)
            
            return self.post_run(result)
        
        except Exception as e:
            import traceback as _tb
            self._status = AgentStatus.FAILED
            error_detail = f"{type(e).__name__}: {str(e)}\n{_tb.format_exc()}"
            return AgentResult(
                status=AgentStatus.FAILED,
                agent_name=self.name,
                error=error_detail,
                duration_seconds=round(_time.time() - start, 2),
            )

    @abstractmethod
    async def execute(self, context: Dict) -> AgentResult:
        """子类必须实现的核心逻辑"""
        ...

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__}({self.name}, status={self.status.value})>"
