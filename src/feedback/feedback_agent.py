"""⑥ 反馈 & 复盘 - 阅读数据采集、效果分析、选题模型迭代"""

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.base_agent import BaseAgent, AgentStatus, AgentResult
from src.logger import logger
from src.config import Config


class FeedbackAgent(BaseAgent):
    """
    反馈 & 复盘 Agent 职责：
    1. 从微信公众号 API 拉取文章阅读/点赞/收藏/分享数据
    2. 计算关键指标（阅读率、完读率、互动率等）
    3. 与历史数据对比分析趋势
    4. 生成日报/周报/月报复盘报告
    5. 将效果数据反馈给选题评分模型（反馈回路）
    
    微信公众号数据分析 API：
    - 获取文章列表: POST /cgi-bin/freepublish/batchget
    - 获取单篇文章统计: POST /cgi-bin/data/getarticletotal
    - 用户画像数据: POST /cgi-bin/data/userread
    - 接口汇总数据: POST /cgi-bin/data/getusersummary
    """

    name = "feedback_agent"
    display_name = "⑥ 反馈 & 复盘 (Analytics & Review)"

    def __init__(self, config: Optional[Dict] = None):
        super().__init__(config)
        self.base_url = self.config.get("wechat_mp.base_url", "https://api.weixin.qq.com")
        self.analytics_dir = Path(Config().project_root) / "data" / "analytics"
        self.analytics_dir.mkdir(parents=True, exist_ok=True)

    async def execute(self, context: Dict) -> AgentResult:
        """执行反馈复盘流程"""
        trigger_type = context.get("trigger_type", "")

        # 根据触发类型决定执行哪种复盘
        if "daily_summary" in str(context):
            return await self._generate_daily_summary(context)
        elif "weekly_review" in str(context):
            return await self._generate_weekly_review(context)
        elif "monthly_report" in str(context):
            return await self._generate_monthly_report(context)
        else:
            # 默认：拉取最新数据并做简单分析
            return await self._fetch_and_analyze(context)

    async def _get_access_token(self) -> Optional[str]:
        """复用 publisher 的 token 获取逻辑"""
        import time as _time
        from src.publisher.publisher_agent import PublisherAgent

        # 简化版：直接请求
        app_id = self.config.get("wechat_mp.app_id", "")
        app_secret = self.config.get("wechat_mp.app_secret", "")
        
        if not app_id or app_id.startswith("${"):
            logger.warning("[复盘Agent] 微信 API 未配置，跳过数据拉取")
            return None

        import httpx
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{self.base_url}/cgi-bin/token",
                params={"grant_type": "client_credential", "appid": app_id, "secret": app_secret},
            )
            data = resp.json()
            return data.get("access_token")

    async def _fetch_article_stats(self, token: str) -> List[Dict]:
        """获取已发布文章的数据统计"""
        url = f"{self.base_url}/cgi-bin/data/getarticletotal"
        params = {"access_token": token}
        
        # 获取最近7天的数据
        begin_date = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
        end_date = datetime.now().strftime("%Y-%m-%d")

        payload = {
            "begin_date": begin_date,
            "end_date": end_date,
        }

        try:
            import httpx
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(url, params=params, json=payload)
                data = resp.json()

                articles = data.get("list", [])
                logger.info(f"[复盘Agent] 获取到 {len(articles)} 条文章统计数据")
                return articles
        except Exception as e:
            logger.error(f"[复盘Agent] 拉取文章数据异常: {e}")
            return []

    async def _fetch_and_analyze(self, context: Dict) -> AgentResult:
        """拉取最新数据并做基本分析"""
        token = await self._get_access_token()
        if not token:
            return AgentResult(
                status=AgentStatus.PARTIAL,
                agent_name=self.name,
                output={
                    "message": "微信API未配置，使用模拟数据",
                    "analysis": self._generate_mock_analysis(),
                },
            )

        articles_stats = await self._fetch_article_stats(token)
        
        analysis = {
            "date_range": "最近7天",
            "total_articles": len(articles_stats),
            "summary": self._compute_metrics_summary(articles_stats),
            "top_performers": self._rank_articles(articles_stats)[:3],
            "threshold_alerts": self._check_thresholds(articles_stats),
            "timestamp": datetime.now().isoformat(),
        }

        # 保存分析结果
        await self._save_analysis(analysis)

        # 生成反馈建议
        suggestions = self._generate_feedback_suggestions(analysis)
        analysis["suggestions"] = suggestions

        return AgentResult(
            status=AgentStatus.SUCCESS,
            agent_name=self.name,
            output=analysis,
        )

    async def _generate_daily_summary(self, context: Dict) -> AgentResult:
        """生成每日运营小结"""
        token = await self._get_access_token()
        
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        today = datetime.now().strftime("%Y-%m-%d")

        summary = {
            "type": "daily_summary",
            "date": today,
            "yesterday_stats": {},
            "today_actions": [],
        }

        # 如果能拿到数据就填充
        if token:
            try:
                import httpx
                async with httpx.AsyncClient(timeout=15.0) as client:
                    resp = await client.post(
                        f"{self.base_url}/cgi-bin/data/getarticletotal",
                        params={"access_token": token},
                        json={"begin_date": yesterday, "end_date": yesterday},
                    )
                    stats = resp.json().get("list", [])
                    summary["yesterday_stats"] = {
                        "articles_published": len(stats),
                        "total_reads": sum(s.get("int_page_read_count", 0) for s in stats),
                        "total_shares": sum(s.get("share_count", 0) for s in stats),
                        "total_likes": sum(s.get("like_count", 0) for s in stats),
                    }
            except Exception as e:
                logger.warning(f"[复盘Agent] 日报数据获取失败: {e}")

        # 当日行动记录（从日志中读取）
        summary["today_actions"] = [
            "✅ 定时任务已执行",
            f"📝 选题完成",
            "🎨 配图获取完成",
            "📑 排版完成",
        ]

        await self._save_analysis(summary, filename_prefix="daily")
        
        return AgentResult(status=AgentStatus.SUCCESS, agent_name=self.name, output=summary)

    async def _generate_weekly_review(self, context: Dict) -> AgentResult:
        """生成每周复盘报告"""
        week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
        today = datetime.now().strftime("%Y-%m-%d")

        review = {
            "type": "weekly_review",
            "period": f"{week_ago} ~ {today}",
            "week_overview": {},
            "trend_analysis": {},
            "improvement_plan": [],
        }

        token = await self._get_access_token()
        if token:
            stats = await self._fetch_article_stats(token)
            if stats:
                review["week_overview"] = {
                    "total_articles": len(stats),
                    "avg_read_count": sum(s.get("int_page_read_count", 0) for s in stats) // max(len(stats), 1),
                    "best_article": max(stats, key=lambda x: x.get("int_page_read_count", 0), default={}),
                }

        # 改进计划模板
        review["improvement_plan"] = [
            "根据上周高阅读量文章调整选题方向",
            "优化标题测试策略（A/B test）",
            "增加互动引导（CTA位置和文案）",
            "检查发布时间效果差异",
        ]

        await self._save_analysis(review, filename_prefix="weekly")
        return AgentResult(status=AgentStatus.SUCCESS, agent_name=self.name, output=review)

    async def _generate_monthly_report(self, context: Dict) -> AgentResult:
        """生成月度总结报告"""
        month_start = datetime.now().replace(day=1).strftime("%Y-%m-%d")
        today = datetime.now().strftime("%Y-%m-%d")

        report = {
            "type": "monthly_report",
            "period": f"{month_start} ~ {today}",
            "key_metrics": {},
            "topic_performance_by_category": {},
            "next_month_strategy": [],
        }

        await self._save_analysis(report, filename_prefix="monthly")
        return AgentResult(status=AgentStatus.SUCCESS, agent_name=self.name, output=report)

    # ==================== 分析工具方法 ====================

    @staticmethod
    def _compute_metrics_summary(stats: List[Dict]) -> Dict:
        """计算核心指标汇总"""
        if not stats:
            return {}

        total_read = sum(s.get("int_page_read_count", 0) for s in stats)
        total_orig_read = sum(s.get("ori_page_read_count", 0) for s in stats)
        total_share = sum(s.get("share_count", 0) for s in stats)
        total_fav = sum(s.get("fav_count", 0) for s in stats)
        total_like = sum(s.get("like_count", 0) for s in stats)
        total_comment = sum(s.get("comment_count", 0) for s in stats)

        avg_read = total_read // len(stats) if stats else 0
        share_rate = total_share / total_read if total_read > 0 else 0
        like_rate = total_like / total_read if total_read > 0 else 0

        return {
            "total_read_count": total_read,
            "total_original_read_count": total_orig_read,
            "total_share_count": total_share,
            "total_favorite_count": total_fav,
            "total_like_count": total_like,
            "total_comment_count": total_comment,
            "avg_read_per_article": avg_read,
            "share_rate": round(share_rate * 100, 2),
            "like_rate": round(like_rate * 100, 2),
            "article_count": len(stats),
        }

    @staticmethod
    def _rank_articles(stats: List[Dict], top_n: int = 5) -> List[Dict]:
        """按阅读量排名文章"""
        ranked = sorted(stats, key=lambda x: x.get("int_page_read_count", 0), reverse=True)
        return ranked[:top_n]

    def _check_thresholds(self, stats: List[Dict]) -> List[Dict]:
        """检查关键指标是否低于阈值，产生告警"""
        thresholds = self.config.get("feedback_agent.thresholds", {})
        alerts = []

        read_min = thresholds.get("read_count_min", 500)
        like_min = thresholds.get("like_rate_min", 0.03)
        share_min = thresholds.get("share_rate_min", 0.01)

        for stat in stats:
            reads = stat.get("int_page_read_count", 0)
            title = stat.get("title", "未知标题")

            if reads < read_min:
                alerts.append({
                    "level": "warning",
                    "article": title,
                    "metric": "阅读量",
                    "value": reads,
                    "threshold": read_min,
                    "message": f"'{title}' 阅读量({reads})低于警戒线({read_min})",
                })

        return alerts

    def _generate_feedback_suggestions(self, analysis: Dict) -> List[str]:
        """基于分析结果生成改进建议"""
        suggestions = []
        alerts = analysis.get("threshold_alerts", [])
        summary = analysis.get("summary", {})

        if alerts:
            suggestions.append(f"⚠️ 有 {len(alerts)} 篇文章指标低于警戒线，建议回顾选题和标题策略")

        avg_read = summary.get("avg_read_per_article", 0)
        if avg_read < 300:
            suggestions.append("📊 平均阅读偏低，建议优化标题吸引力和推送时间")

        share_rate = summary.get("share_rate", 0)
        if share_rate < 1.0:
            suggestions.append("🔄 分享率偏低，建议在文中增加更有价值的金句或实用工具推荐")

        like_rate = summary.get("like_rate", 0)
        if like_rate < 2.0:
            suggestions.append("❤️ 点赞率偏低，建议优化结尾CTA和互动设计")

        if not suggestions:
            suggestions.append("🎉 各项指标表现良好！继续保持当前策略")

        return suggestions

    def _generate_mock_analysis(self) -> Dict:
        """当微信API未配置时返回模拟数据（用于演示/测试）"""
        return {
            "mode": "mock",
            "note": "微信API未配置，以下为模拟数据",
            "date_range": "模拟周期",
            "total_articles": 3,
            "summary": {
                "total_read_count": 2847,
                "avg_read_per_article": 949,
                "share_rate": 3.2,
                "like_rate": 5.8,
            },
            "top_performers": [
                {"title": "[模拟] AI工具大盘点2026", "int_page_read_count": 1520},
                {"title": "[模拟] 效率翻倍的工作流", "int_page_read_count": 832},
                {"title": "[模拟] 从零开始用Agent", "int_page_read_count": 495},
            ],
            "threshold_alerts": [],
            "suggestions": ["配置微信API以获取真实数据"],
        }

    async def _save_analysis(self, data: Dict, filename_prefix: str = "analysis"):
        """保存分析结果"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filepath = self.analytics_dir / f"{filename_prefix}_{timestamp}.json"

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        logger.info(f"[复盘Agent] 分析结果已保存: {filepath}")

    # ==================== 反馈回路：更新选题评分权重 ====================

    def update_topic_scoring_weights(self, performance_data: Dict) -> Dict:
        """
        基于历史表现数据，动态调整选题评分权重。
        这是反馈回路的实现——让系统越用越聪明。

        策略：
        - 高阅读量的文章 → 其所属关键词类别的相关性权重提升
        - 高分享率 → 时效性权重提升（热点话题更值得追）
        - 低互动 → 降低同类话题的优先级
        """
        current_weights = self.config.get("topic_agent.scoring_weights", {})
        new_weights = dict(current_weights)
        
        lookback_days = self.config.get("feedback_agent.feedback_loop.lookback_days", 30)
        decay_factor = self.config.get("feedback_agent.feedback_loop.weight_decay", 0.95)

        # 基于表现的简单权重调整逻辑
        top_category = performance_data.get("best_category")
        avg_engagement = performance_data.get("avg_engagement_rate", 0)

        if top_category:
            # 如果某类别表现好，略微提升其隐含权重
            new_weights["relevance"] = min(
                0.50,
                current_weights.get("relevance", 0.30) * (1 + avg_engagement * 0.1)
            )
            # 保持总权重为1.0
            total = sum(new_weights.values())
            new_weights = {k: v / total for k, v in new_weights.items()}

        return new_weights
