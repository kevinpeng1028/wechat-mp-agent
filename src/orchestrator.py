"""
主编算器 - 串联所有 Agent，管理完整运营流程（韩国娱乐圈版）

新流程：
  触发 → ①选题(韩国娱乐搜索+5篇预选+评分) → 选最高2篇(头条+次条)
       → 对每篇文章: ②写作 → ③配图(下载Tavily源图) → ④排版(模板+图文检查)
       → ⑤发布(封面/正文图上传+多图文草稿)
"""

import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

from src.config import Config
from src.logger import logger
from src.base_agent import AgentStatus, AgentResult

# 导入所有 Agent
from src.trigger.scheduler import TriggerLayer
from src.topic.topic_agent import TopicAgent
from src.writer.writing_agent import WritingAgent
from src.image.image_agent import ImageAgent
from src.formatter.formatting_agent import FormattingAgent
from src.publisher.publisher_agent import PublisherAgent
from src.feedback.feedback_agent import FeedbackAgent


console = Console()


class WeChatMPOrchestrator:
    """
    微信公众号运营主编算器（韩国娱乐圈版）

    核心流程：
    1. 选题: Tavily搜索韩国娱乐媒体 → 5篇预选 → 综合评分 → 最高2篇
    2. 写作: 对2篇文章分别用LLM改写（爱豆状态观察文风）
    3. 配图: 下载Tavily源文章配图（确保图文绑定）
    4. 排版: 模板系统 + 段落拆分 + 图文一致性检查
    5. 发布: 上传封面/正文图 → 创建多图文草稿（头条+次条）
    6. 复盘: 数据分析 → 反馈到选题模型
    """

    def __init__(self, config: Optional[Config] = None):
        self.config = config or Config()
        # 如果未传入外部配置，则重新加载配置（确保使用最新的 config.yaml）
        if not config:
            self.config.reload()

        # 设置 project_root（项目根目录）
        project_root = str(Path(__file__).parent.parent)
        self.config.data["project_root"] = project_root

        # 初始化所有 Agent
        self.trigger = TriggerLayer(self.config.data)
        self.topic_agent = TopicAgent(self.config.data)
        self.writer = WritingAgent(self.config.data)
        self.image_agent = ImageAgent(self.config.data)
        self.formatter = FormattingAgent(self.config.data)
        self.publisher = PublisherAgent(self.config.data)
        self.feedback = FeedbackAgent(self.config.data)

        # 运行记录
        self.run_history: List[Dict] = []
        self.current_context: Dict = {}

    async def run_full_pipeline(
        self,
        trigger_context: Optional[Dict] = None,
        manual_mode: bool = False,
    ) -> Dict:
        """
        执行完整的运营流水线

        Args:
            trigger_context: 触发层传入的上下文
            manual_mode: 手动模式（每步暂停确认）
        """
        ctx = trigger_context or {"trigger_type": "manual"}
        ctx["started_at"] = datetime.now().isoformat()
        self.current_context = ctx

        report = {
            "run_id": f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
            "trigger_type": ctx.get("trigger_type", "unknown"),
            "started_at": ctx["started_at"],
            "steps": [],
            "final_status": "unknown",
            "error": None,
        }

        console.print(Panel.fit(
            "[bold cyan]🚀 微信公众号全自动运营 Agent[/bold cyan]\n"
            f"韩国娱乐圈版 | 运行 ID: {report['run_id']}",
            title="WeChat MP Agent v2.0",
            border_style="cyan",
        ))

        # ==================== 流程开始 ====================
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            TimeElapsedColumn(),
            console=console,
        ) as progress:

            # ---- Step 1: 选题 ----
            task1 = progress.add_task("[bold blue]① 选题 Agent[/bold blue]", total=None)

            if manual_mode:
                await self._confirm_step("选题（韩国娱乐媒体搜索+评分）")

            topic_result = await self.topic_agent.run(ctx)
            report["steps"].append(self._result_to_dict(topic_result))

            if not topic_result.is_success:
                console.print(f"[red]❌ 选题失败: {self._short_error(topic_result.error)}[/red]")
                report["final_status"] = "failed_at_topic"
                return self._finalize_report(report)

            ctx.update(topic_result.output or {})
            progress.update(task1, completed=True)

            selected = ctx.get("selected_topics", [])
            console.print(
                f"  ✅ 选题完成: {len(selected)} 篇文章入选 "
                f"(头条: {selected[0].get('title','?')[:30] if selected else '?'})"
            )

            # ---- Step 2: 写作（对每篇入选文章）----
            task2 = progress.add_task("[bold green]② 写作 Agent[/bold green]", total=None)

            if manual_mode:
                await self._confirm_step("写作（爱豆状态观察文风）")

            # ---- Step 3: 配图（下载Tavily源文章配图）----
            task3 = progress.add_task("[bold magenta]③ 配图 Agent[/bold magenta]", total=None)

            if manual_mode:
                await self._confirm_step("配图（下载Tavily源文章配图）")

            fallback_topics = ctx.get("ready_articles") or selected
            max_attempts = self.config.get(
                "topic_agent.image.max_article_image_attempts", 5
            )
            written, images, fallback_results = await self._write_with_image_fallback(
                ctx,
                fallback_topics,
                max_attempts=max_attempts,
                target_count=self.config.get("topic_agent.search.selected_count", 2),
            )
            report["candidate_attempts"] = getattr(
                self, "_last_candidate_attempts", []
            )
            for result in self._aggregate_fallback_results():
                report["steps"].append(self._result_to_dict(result))

            if not written:
                failures = getattr(self, "_last_fallback_failures", {})
                attempted_count = min(len(fallback_topics), max_attempts)
                status, error = self._fallback_failure_outcome(
                    failures, attempted_count
                )
                report["final_status"] = status
                report["error"] = error
                logger.error(f"[编排器] {report['error']}")
                return self._finalize_report(report)

            ctx["written_articles"] = written
            ctx["downloaded_images"] = images
            ctx["selected_topics"] = [a.get("topic_info", {}) for a in written]
            budget = ctx.get("tavily_budget", {})
            if budget:
                logger.info(
                    f"[编排器] Tavily最终成本 | 实际调用={budget.get('calls', 0)} | "
                    f"预计credits={budget.get('credits', 0)}/"
                    f"{budget.get('max_total_credits', 12)} | "
                    f"缓存命中={budget.get('cache_hits', 0)} | "
                    f"剩余预算={max(0, budget.get('max_total_credits', 12)-budget.get('credits', 0))} | "
                    f"hard stop={budget.get('hard_stop_triggered', False)}"
                )
            progress.update(task2, completed=True)
            progress.update(task3, completed=True)
            console.print(
                f"  ✅ 写作/配图完成: {len(written)} 篇文章，下载 {len(images)} 张图片"
            )

            # ---- Step 4: 排版 ----
            task4 = progress.add_task("[bold yellow]④ 排版 Agent[/bold yellow]", total=None)

            if manual_mode:
                await self._confirm_step("排版（模板+图文一致性检查）")

            # 排版Agent需要 formatted_articles
            # 从写作结果传递
            ctx["formatted_articles"] = self._prepare_for_formatting(written, ctx)

            format_result = await self.formatter.run(ctx)
            report["steps"].append(self._result_to_dict(format_result))

            if not format_result.is_success:
                console.print(f"[red]❌ 排版失败: {self._short_error(format_result.error)}[/red]")
                report["final_status"] = "failed_at_formatting"
                return self._finalize_report(report)

            ctx.update(format_result.output or {})
            progress.update(task4, completed=True)

            fmt_articles = format_result.output.get("formatted_articles", []) if format_result.output else []
            console.print(f"  ✅ 排版完成: {len(fmt_articles)} 篇 | 模板: {fmt_articles[0].get('template_used', '?') if fmt_articles else '?'}")
            # ---- Step 5: 发布 ----
            task5 = progress.add_task("[bold red]⑤ 发布 Agent[/bold red]", total=None)

            if manual_mode:
                await self._confirm_step("发布（上传图片+创建草稿）")

            # 发布Agent需要 formatted_articles
            ctx["formatted_articles"] = ctx.get("formatted_articles", fmt_articles)

            publish_result = await self.publisher.run(ctx)
            report["steps"].append(self._result_to_dict(publish_result))

            if publish_result.is_success:
                output = publish_result.output or {}
                media_id = output.get("draft_media_id", "")
                article_count = output.get("article_count", 0)

                console.print(f"  ✅ 发布完成!")
                console.print(f"     草稿ID: [cyan]{media_id}[/cyan]")
                console.print(f"     文章数: {article_count}（头条+次条）")
                console.print(f"     📝 [yellow]草稿已存入草稿箱，请登录公众号后台查看[/yellow]")
            else:
                console.print(f"[red]❌ 发布失败: {self._short_error(publish_result.error)}[/red]")
                report["final_status"] = "failed_at_publisher"

            ctx.update(publish_result.output or {})
            progress.update(task5, completed=True)

            # ---- Step 6: 反馈复盘（可选）----
            try:
                task6 = progress.add_task("[bold white]⑥ 反馈 & 复盘[/bold white]", total=None)
                feedback_result = await self.feedback.run(ctx)
                report["steps"].append(self._result_to_dict(feedback_result))
                progress.update(task6, completed=True)
            except Exception as e:
                logger.warning(f"复盘步骤异常(不影响主流程): {e}")

        # ==================== 完成 ====================
        report["finished_at"] = datetime.now().isoformat()
        report["final_status"] = "success"
        report["context_snapshot"] = {
            "selected_count": len(ctx.get("selected_topics", [])),
            "written_count": len(ctx.get("written_articles", [])),
            "draft_media_id": ctx.get("draft_media_id", ""),
        }

        self._save_run_report(report)
        self._print_final_summary(report)

        self.run_history.append(report)
        return report

    def _prepare_for_formatting(self, written_articles: List[Dict], ctx: Dict) -> List[Dict]:
        """准备排版Agent所需的数据格式"""
        # 获取已下载的图片（含本地 path），建立 url → path 映射
        downloaded = ctx.get("downloaded_images", [])
        url_to_download = {}
        for img in downloaded:
            if isinstance(img, dict):
                url = img.get("url") or img.get("source_url", "")
                path = img.get("path", "")
                if url and path:
                    img["url"] = url
                    url_to_download[url] = img

        formatted = []
        for article in written_articles:
            if not article.get("is_success"):
                continue
            # 合并已下载图片的本地路径到 tavily_images
            tavily_images = article.get("tavily_images", [])
            for img in tavily_images:
                if isinstance(img, dict) and not img.get("path"):
                    img_url = img.get("url", "")
                    if img_url in url_to_download:
                        # 传递视觉质量指标，供排版前一致性检查再次验收。
                        img.update(url_to_download[img_url])
            formatted.append({
                "is_success": True,
                "title": article.get("title", ""),
                "summary": article.get("summary", ""),
                "content_text": article.get("content_text", ""),
                "content_html": article.get("content_html", ""),
                "tavily_images": tavily_images,
                "topic_info": article.get("topic_info", {}),
                "position": article.get("position", "unknown"),
            })
        return formatted

    async def _write_with_image_fallback(
        self,
        ctx: Dict,
        candidates: List[Dict],
        max_attempts: int = 5,
        target_count: int = 2,
    ):
        """逐篇写作并验证图片；失败时切换下一候选。"""
        successful_articles = []
        successful_images = []
        results = []
        attempted = candidates[:max_attempts]
        failure_counts = {"writing": 0, "image": 0, "formatting": 0}
        candidate_attempts = []

        for index, topic in enumerate(attempted, start=1):
            title = topic.get("title", "?")
            logger.info(
                f"[编排器] 图片候选尝试 {index}/{len(attempted)}: {title[:80]}"
            )
            single_ctx = dict(ctx)
            single_ctx["selected_topics"] = [topic]
            writer_result = await self.writer.run(single_ctx)
            results.append(writer_result)
            if not writer_result.is_success:
                failure_counts["writing"] += 1
                topic["write_failed"] = True
                topic["write_failure_reason"] = writer_result.error
                candidate_attempts.append({
                    "topic_title": title,
                    "write_status": "failed",
                    "image_status": "not_run",
                    "failure_reason": writer_result.error or "写作失败",
                })
                logger.warning(f"[编排器] 写作失败，切换下一篇: {title[:80]}")
                continue

            article = (writer_result.output or {}).get("written_articles", [None])[0]
            if not article:
                failure_counts["writing"] += 1
                topic["write_failed"] = True
                topic["write_failure_reason"] = "写作结果缺少文章内容"
                candidate_attempts.append({
                    "topic_title": title,
                    "write_status": "failed",
                    "image_status": "not_run",
                    "failure_reason": "写作结果缺少文章内容",
                })
                continue
            image_ctx = dict(ctx)
            image_ctx["tavily_images"] = article.get("tavily_images", [])
            image_ctx["topic_info"] = article.get("topic_info", topic)
            image_result = await self.image_agent.run(image_ctx)
            results.append(image_result)
            images = (image_result.output or {}).get("images", [])

            if not image_result.is_success or not images:
                failure_counts["image"] += 1
                topic["image_failed"] = True
                topic["image_failure_reason"] = (
                    image_result.error
                    or (image_result.output or {}).get("message")
                    or "没有有效图片"
                )
                logger.warning(
                    f"[编排器] 图片失败: {title[:80]} | "
                    f"{topic['image_failure_reason']} | 切换下一篇"
                )
                candidate_attempts.append({
                    "topic_title": title,
                    "write_status": "success",
                    "image_status": "failed",
                    "failure_reason": topic["image_failure_reason"],
                })
                continue

            position = "headline" if not successful_articles else "sub_headline"
            article["position"] = position
            article["topic_info"]["position"] = position
            article["tavily_images"] = images
            article["cover_only_mode"] = bool(
                (image_result.output or {}).get("cover_only_mode")
            )
            successful_articles.append(article)
            successful_images.extend(images)
            candidate_attempts.append({
                "topic_title": title,
                "write_status": "success",
                "image_status": "success",
                "failure_reason": "",
            })
            logger.info(
                f"[编排器] ✅ 图文通过: {title[:80]} | 图片 {len(images)} 张"
            )
            if len(successful_articles) >= target_count:
                break

        # 所有常规写作都失败时，用最高分候选做一次确定性的安全整理，
        # 避免轻微校验问题让整条生产链停在 writing。
        if not successful_articles and attempted and hasattr(
            self.writer, "build_safe_fallback_article"
        ):
            topic = attempted[0]
            fallback_article = self.writer.build_safe_fallback_article(topic)
            if fallback_article.get("is_success"):
                failure_counts["writing"] = max(
                    0, failure_counts["writing"] - 1
                )
                logger.warning(
                    f"[编排器] 常规写作均未产出，启用最高分候选 safe fallback: "
                    f"{topic.get('title', '')[:80]}"
                )
                if hasattr(self.writer, "_save_draft"):
                    await self.writer._save_draft(fallback_article)
                image_ctx = dict(ctx)
                image_ctx["tavily_images"] = fallback_article.get(
                    "tavily_images", []
                )
                image_ctx["topic_info"] = topic
                image_result = await self.image_agent.run(image_ctx)
                results.append(AgentResult(
                    status=AgentStatus.SUCCESS,
                    agent_name="writer_agent",
                    output={"written_articles": [fallback_article]},
                ))
                results.append(image_result)
                images = (image_result.output or {}).get("images", [])
                candidate_attempts.append({
                    "topic_title": topic.get("title", ""),
                    "write_status": "success",
                    "image_status": (
                        "success" if image_result.is_success and images
                        else "failed"
                    ),
                    "failure_reason": (
                        "" if image_result.is_success and images
                        else image_result.error or "safe fallback 图片失败"
                    ),
                })
                if image_result.is_success and images:
                    fallback_article["position"] = "headline"
                    fallback_article["topic_info"]["position"] = "headline"
                    fallback_article["tavily_images"] = images
                    fallback_article["cover_only_mode"] = bool(
                        (image_result.output or {}).get("cover_only_mode")
                    )
                    successful_articles.append(fallback_article)
                    successful_images.extend(images)
                else:
                    failure_counts["image"] += 1

        self._last_fallback_failures = failure_counts
        self._last_candidate_attempts = candidate_attempts
        return successful_articles, successful_images, results

    def _aggregate_fallback_results(self) -> List[AgentResult]:
        attempts = getattr(self, "_last_candidate_attempts", [])
        write_success = sum(
            item.get("write_status") == "success" for item in attempts
        )
        write_failed = sum(
            item.get("write_status") == "failed" for item in attempts
        )
        image_success = sum(
            item.get("image_status") == "success" for item in attempts
        )
        image_failed = sum(
            item.get("image_status") == "failed" for item in attempts
        )
        writing_status = (
            AgentStatus.PARTIAL if write_success and write_failed
            else AgentStatus.SUCCESS if write_success
            else AgentStatus.FAILED
        )
        image_status = (
            AgentStatus.PARTIAL if image_success and image_failed
            else AgentStatus.SUCCESS if image_success
            else AgentStatus.FAILED
        )
        results = [
            AgentResult(
                status=writing_status,
                agent_name="writer_agent",
                output={"success": write_success, "failed": write_failed},
                error=(
                    "部分候选写作失败，已切换下一篇"
                    if writing_status == AgentStatus.PARTIAL
                    else "所有候选写作均失败"
                    if writing_status == AgentStatus.FAILED
                    else None
                ),
            )
        ]
        if image_success or image_failed:
            results.append(AgentResult(
                status=image_status,
                agent_name="image_agent",
                output={"success": image_success, "failed": image_failed},
                error=(
                    "部分候选图片失败"
                    if image_status == AgentStatus.PARTIAL
                    else "所有已写文章图片均失败"
                    if image_status == AgentStatus.FAILED
                    else None
                ),
            ))
        return results

    @staticmethod
    def _fallback_failure_outcome(
        failures: Dict[str, int], attempted_count: int
    ):
        if failures.get("writing", 0) == attempted_count:
            return (
                "failed_at_writing",
                f"前 {attempted_count} 篇候选写作均失败，终止流程",
            )
        if failures.get("image", 0) > 0:
            return (
                "failed_at_image",
                f"前 {attempted_count} 篇候选图片均失败，"
                "终止流程，不进入排版/草稿",
            )
        return (
            "failed_at_writing",
            f"前 {attempted_count} 篇候选未能完成写作，终止流程",
        )

    # ==================== 辅助方法 ====================

    async def _confirm_step(self, step_name: str):
        """手动模式下暂停等待确认"""
        console.print(f"\n[yellow]⏸️  即将执行「{step_name}」...[/yellow]")
        if console.input("[dim]按 Enter 继续，输入 n 跳过: [/dim]").strip().lower() == "n":
            raise RuntimeError(f"用户跳过了{step_name}步骤")

    def _result_to_dict(self, result: AgentResult) -> Dict:
        """AgentResult → Dict"""
        return {
            "status": result.status.value,
            "agent_name": result.agent_name,
            "output": result.output,
            "error": result.error,
            "duration_seconds": result.duration_seconds,
            "timestamp": result.timestamp.isoformat(),
        }

    def _short_error(self, error: Optional[str]) -> str:
        if not error:
            return "?"
        return error[:100]

    def _save_run_report(self, report: Dict):
        """保存运行报告"""
        reports_dir = Path(self.config.get("project_root", ".")) / "data" / "analytics"
        reports_dir.mkdir(parents=True, exist_ok=True)

        run_id = report.get("run_id", "unknown")
        filepath = reports_dir / f"{run_id}_report.json"

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)

        logger.info(f"运行报告已保存: {filepath}")

    def _print_final_summary(self, report: Dict):
        """打印最终执行摘要"""
        table = Table(title="📊 运行摘要", show_header=True)
        table.add_column("步骤", style="bold")
        table.add_column("状态", justify="center")
        table.add_column("耗时", justify="right")
        table.add_column("备注")

        step_names = [
            ("① 选题", "topic_agent"),
            ("② 写作", "writer_agent"),
            ("③ 配图", "image_agent"),
            ("④ 排版", "formatter_agent"),
            ("⑤ 发布", "publisher_agent"),
            ("⑥ 复盘", "feedback_agent"),
        ]

        for display_name, agent_key in step_names:
            step_data = next(
                (s for s in report.get("steps", []) if s.get("agent_name") == agent_key),
                None,
            )

            if step_data:
                status = step_data.get("status", "unknown")
                duration = step_data.get("duration_seconds", 0)
                error_short = (step_data.get("error", "") or "")[:40]

                icon = {"success": "✅", "partial": "⚠️", "failed": "❌"}.get(status, "❓")
                color = {"success": "green", "partial": "yellow", "failed": "red"}.get(status, "white")

                table.add_row(
                    display_name,
                    f"[{color}]{icon} {status}[/{color}]",
                    f"{duration:.1f}s",
                    error_short if status == "failed" else "-",
                )
            else:
                table.add_row(display_name, "[dim]-[/dim]", "-", "[dim]未执行[/dim]")

        console.print(table)

        start = datetime.fromisoformat(report["started_at"])
        end_str = report.get("finished_at") or datetime.now().isoformat()
        end = datetime.fromisoformat(end_str)
        total_seconds = (end - start).total_seconds()
        console.print(f"\n[dim]总耗时: {total_seconds:.1f}s | 状态: {report['final_status']}[/dim]\n")

    def _finalize_report(self, report: Dict) -> Dict:
        """终结化报告"""
        report["finished_at"] = datetime.now().isoformat()
        self._save_run_report(report)
        self._print_final_summary(report)
        self.run_history.append(report)
        return report

    async def cleanup(self):
        """清理所有Agent的资源"""
        for agent in [
            self.topic_agent,
            self.writer,
            self.image_agent,
            self.publisher,
        ]:
            if hasattr(agent, "cleanup"):
                try:
                    await agent.cleanup()
                except Exception as e:
                    logger.warning(f"清理 {agent.name} 失败: {e}")


# ==================== 快捷入口 ====================

async def run_once(config_override: Optional[Dict] = None):
    """单次手动触发（测试用）"""
    orchestrator = WeChatMPOrchestrator()
    return await orchestrator.run_full_pipeline(manual_mode=False)


async def run_manual():
    """手动交互模式"""
    orchestrator = WeChatMPOrchestrator()
    return await orchestrator.run_full_pipeline(manual_mode=True)


async def run_scheduled():
    """定时调度模式"""
    config = Config()
    orchestrator = WeChatMPOrchestrator(config)

    async def on_trigger(trigger_ctx: Dict):
        await orchestrator.run_full_pipeline(trigger_context=trigger_ctx, manual_mode=False)

    orchestrator.trigger.on_trigger = on_trigger

    init_result = await orchestrator.trigger.run({})
    if init_result.status != AgentStatus.SUCCESS:
        console.print("[red]触发层初始化失败！[/red]")
        return

    console.print("\n[bold green]✅ 微信公众号运营 Agent 已启动！[/bold green]")
    console.print("[dim]按 Ctrl+C 停止...[/dim]\n")

    try:
        while True:
            await asyncio.sleep(3600)
    except KeyboardInterrupt:
        console.print("\n[yellow]收到停止信号，正在关闭...[/yellow]")
        await orchestrator.cleanup()
        console.print("[green]已安全关闭。[/green]")
