"""触发层 - 定时任务 / 手动触发 / 热点事件触发"""

import asyncio
import signal
from typing import Callable, Dict, List, Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from src.base_agent import BaseAgent, AgentStatus, AgentResult
from src.logger import logger
from src.config import Config


# 全局调度器实例
_scheduler: Optional[AsyncIOScheduler] = None


def get_scheduler() -> AsyncIOScheduler:
    """获取全局单例调度器"""
    global _scheduler
    if _scheduler is None:
        _scheduler = AsyncIOScheduler(
            timezone="Asia/Shanghai",
            job_defaults={
                "coalesce": True,       # 合并错过的执行
                "max_instances": 1,      # 同一job不并发
                "misfire_grace_time": 300,  # 误执行宽容5分钟
            },
        )
    return _scheduler


class TriggerLayer(BaseAgent):
    """触发层 Agent - 负责启动整个运营流程"""

    name = "trigger_layer"
    display_name = "触发层 (Trigger Layer)"

    def __init__(self, config: Optional[Dict] = None, on_trigger: Optional[Callable] = None):
        super().__init__(config)
        self.on_trigger = on_trigger  # 触发后的回调函数
        self.scheduler = get_scheduler()

    async def execute(self, context: Dict) -> AgentResult:
        """启动调度系统并注册任务"""
        trigger_cfg = self.config.get("trigger", {})

        # 注册定时任务
        schedule_cfg = trigger_cfg.get("schedule", {})
        if schedule_cfg.get("enabled", True):
            cron_expr = schedule_cfg.get("cron", "0 9 * * 1,3,5")
            await self._register_cron_job(cron_expr)

        # 注册热点检测任务（如果启用）
        hot_event_cfg = trigger_cfg.get("hot_event", {})
        if hot_event_cfg.get("enabled", False):
            interval = hot_event_cfg.get("check_interval_minutes", 60)
            await self._register_hot_event_job(interval)

        # 启动调度器
        if not self.scheduler.running:
            self.scheduler.start()
            logger.info("[触发层] 调度器已启动")

        # 同时支持立即手动触发一次
        if context.get("manual_trigger", False):
            await self._fire_callback(context)

        return AgentResult(
            status=AgentStatus.SUCCESS,
            agent_name=self.name,
            output={
                "message": "触发层初始化完成",
                "cron_schedule": schedule_cfg.get("cron"),
                "hot_event_enabled": hot_event_cfg.get("enabled", False),
            },
        )

    async def _register_cron_job(self, cron_expr: str):
        """注册 cron 定时任务"""
        parts = cron_expr.split()
        if len(parts) != 6:
            raise ValueError(f"无效的 cron 表达式(需6段): {cron_expr}")

        trigger = CronTrigger(
            minute=parts[0],
            hour=parts[1],
            day=parts[2],
            month=parts[3],
            day_of_week=parts[4],
            year=parts[5] if len(parts) > 5 else "*",
        )

        self.scheduler.add_job(
            self._fire_callback,
            trigger=trigger,
            id="scheduled_publish",
            name="定时发布任务",
            replace_existing=True,
        )
        logger.info(f"[触发层] 已注册定时任务: {cron_expr}")

    async def _register_hot_event_job(self, interval_minutes: int):
        """注册热点检测定时任务"""
        trigger = IntervalTrigger(minutes=interval_minutes)

        self.scheduler.add_job(
            self._check_and_fire_hot_event,
            trigger=trigger,
            id="hot_event_check",
            name=f"热点事件检测(每{interval_minutes}分钟)",
            replace_existing=True,
        )
        logger.info(f"[触发层] 已注册热点检测: 每{interval_minutes}分钟")

    async def _fire_callback(self, context: Optional[Dict] = None):
        """触发回调 - 启动完整运营流程"""
        ctx = context or {"trigger_type": "scheduled"}
        logger.info(f"[触发层] 🚀 流程触发! 类型: {ctx.get('trigger_type', 'unknown')}")
        if self.on_trigger:
            await self.on_trigger(ctx)

    async def _check_and_fire_hot_event(self):
        """检查热点事件，达到阈值则触发"""
        try:
            hot_events = await self._fetch_hot_events()
            for event in hot_events:
                score = event.get("score", 0)
                threshold = self.config.get("trigger.hot_event.threshold_score", 8000)
                if score >= threshold:
                    logger.info(f"[触发层] 🔥 热点事件触发! '{event['title']}' 热度={score}")
                    await self._fire_callback({
                        "trigger_type": "hot_event",
                        "hot_event": event,
                    })
                    break  # 每次只触发一个
        except Exception as e:
            logger.error(f"[触发层] 热点检测异常: {e}")

    async def _fetch_hot_events(self) -> List[Dict]:
        """获取热点事件列表（可接入微博热搜/百度热搜等API）"""
        # TODO: 接入实际的热点 API
        # 这里返回空列表作为占位
        return []

    def shutdown(self):
        """关闭调度器"""
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
            logger.info("[触发层] 调度器已关闭")

    async def manual_trigger(self, extra_context: Optional[Dict] = None):
        """手动触发一次完整流程"""
        ctx = {"trigger_type": "manual"}
        if extra_context:
            ctx.update(extra_context)
        await self._fire_callback(ctx)


async def setup_signal_handlers(trigger: TriggerLayer):
    """注册优雅关闭信号处理"""
    loop = asyncio.get_running_loop()

    def _shutdown(signum, frame):
        logger.warning("收到终止信号，正在优雅关闭...")
        trigger.shutdown()

    # Windows 不支持 SIGTERM/SIGINT via signal.signal 的某些方式
    try:
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, lambda s=sig: _shutdown(s, None))
            except (NotImplementedError, OSError):
                pass
    except Exception:
        pass
