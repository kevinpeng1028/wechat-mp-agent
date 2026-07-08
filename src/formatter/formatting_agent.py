"""④ 排版 Agent - 模板系统 + 段落拆分 + 图片插入规则 + 图文一致性检查"""

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.base_agent import BaseAgent, AgentStatus, AgentResult
from src.config import Config
from src.formatter.template_manager import TemplateManager
from src.logger import logger
from src.image.image_consistency import ImageConsistencyChecker


class FormattingAgent(BaseAgent):
    """
    排版 Agent 职责：
    1. 接收写作Agent的输出（正文+图片）
    2. 图文一致性检查（低于85分不允许进入ready）
    3. 使用模板系统渲染HTML（或系统默认排版）
    4. 段落拆分（40-90字，超120自动拆分）
    5. 图片插入规则（1-4张图的位置规则）
    6. 所有样式用 inline style，兼容微信公众号编辑器
    7. 输出完整HTML供发布Agent使用

    检查规则：
    - 文章人物和图片人物必须一致
    - 文章组合和图片组合必须一致
    - 场景必须匹配（机场≠舞台）
    - 一致性低于85分 → 不允许生成微信草稿
    """

    name = "formatter_agent"
    display_name = "④ 排版 Agent (模板+段落+图片规则)"

    def __init__(self, config: Optional[Dict] = None):
        super().__init__(config)
        self.template_mgr = TemplateManager(config or {})
        self.consistency_checker = ImageConsistencyChecker(config or {})
        self.paragraph_rules = self.get_config(
            "formatter_agent.paragraph_split", {}
        )

    async def execute(self, context: Dict) -> AgentResult:
        """
        执行排版流程

        context 中包含:
        - written_articles: 写作Agent输出的文章列表
        - tavily_images: 图片列表
        """
        written_articles = context.get("written_articles", [])
        if not written_articles:
            return AgentResult(
                status=AgentStatus.FAILED,
                agent_name=self.name,
                error="缺少 written_articles",
            )

        logger.info(f"[排版Agent] 开始排版 {len(written_articles)} 篇文章...")

        formatted_articles = []

        for i, article in enumerate(written_articles):
            logger.info(
                f"[排版Agent] 文章 {i+1}/{len(written_articles)}: "
                f"'{article.get('title','?')[:40]}'"
            )

            result = await self._format_single(article, context)
            formatted_articles.append(result)

        # 合并结果
        success_count = sum(1 for r in formatted_articles if r.get("is_success"))

        if success_count == 0:
            return AgentResult(
                status=AgentStatus.FAILED,
                agent_name=self.name,
                error="所有文章排版均失败",
            )

        # 保存排版结果
        for article in formatted_articles:
            if article.get("is_success"):
                await self._save_html(article)

        logger.info(
            f"[排版Agent] ✅ 完成! 成功 {success_count}/{len(written_articles)} 篇"
        )

        return AgentResult(
            status=AgentStatus.SUCCESS,
            agent_name=self.name,
            output={
                "formatted_articles": formatted_articles,
                "success_count": success_count,
                "total_count": len(written_articles),
            },
        )

    async def _format_single(self, article: Dict, context: Dict) -> Dict:
        """对单篇文章执行排版"""
        title = article.get("title", "")
        content_text = article.get("content_text", "")
        summary = article.get("summary", "")
        tavily_images = article.get("tavily_images", [])
        topic_info = article.get("topic_info") or {}

        # Step 1: 图文一致性检查
        consistency = self.consistency_checker.check_consistency(
            topic_info, tavily_images
        )

        if not consistency.get("passed"):
            issues = consistency.get("issues", [])
            logger.warning(
                f"[排版Agent] ❌ 图文一致性未通过(得分{consistency.get('consistency_score',0)}): "
                f"{issues}"
            )
            return {
                "is_success": False,
                "title": title,
                "error": f"图文一致性未通过: {issues}",
                "consistency_score": consistency.get("consistency_score", 0),
            }

        logger.info(
            f"[排版Agent] ✅ 图文一致性通过: {consistency.get('consistency_score', 0):.0f}分"
        )

        # 使用过滤后的有效图片
        valid_images = consistency.get("valid_images", tavily_images)
        cover_image = valid_images[0] if valid_images else None
        cover_only_mode = (
            len(valid_images) == 1
            and self.get_config("topic_agent.image.allow_cover_only_mode", True)
        )
        cover_in_body = self.get_config(
            "formatter_agent.image_insertion.cover_in_body", False
        )
        if cover_in_body:
            render_images = valid_images
        else:
            render_images = [
                img for img in valid_images
                if img.get("position") not in ("cover", "thumb", "cover_image")
            ]
        if cover_only_mode:
            render_images = []
            logger.warning("[排版Agent] ⚠️ 仅1张有效图片，进入封面图模式")
            logger.info("[排版Agent] 正文图为空，继续排版")

        # Step 2: 提取导语和结尾
        intro = self._extract_intro(content_text)
        ending = self._extract_ending(content_text)

        # Step 3: 从正文中移除导语和结尾文本，避免重复
        body_text = content_text

        # 移除导语（从开头移除）
        if intro:
            intro_clean = intro.rstrip("。")
            if body_text.startswith(intro_clean):
                body_text = body_text[len(intro_clean):].lstrip("。").strip()
            else:
                # 尝试移除导语的第一句（可能导语包含2句，但正文只以第1句开头）
                first_sentence = intro_clean.split("。")[0]
                if first_sentence and body_text.startswith(first_sentence):
                    body_text = body_text[len(first_sentence):].lstrip("。").strip()

        # 移除结尾（从末尾移除）
        if ending:
            ending_clean = ending.rstrip("。")
            if body_text.endswith(ending_clean):
                body_text = body_text[:body_text.rfind(ending_clean)].rstrip("。").strip()
            else:
                # 结尾可能是最后一个段落的一部分，尝试从最后一个句号后移除
                last_period_idx = body_text.rfind("。")
                if last_period_idx > 0:
                    last_sentence = body_text[last_period_idx + 1:].strip()
                    if last_sentence and ending_clean.startswith(last_sentence):
                        body_text = body_text[:last_period_idx + 1].strip()

        # Step 4: 拆分正文段落（使用去重后的文本）
        paragraphs = self.template_mgr.split_paragraphs(
            body_text,
            min_chars=self.paragraph_rules.get("min_chars", 40),
            max_chars=self.paragraph_rules.get("max_chars", 90),
            split_threshold=self.paragraph_rules.get("split_threshold", 120),
        )

        # 再次检查：移除末尾与结尾重复的段落
        if ending and paragraphs:
            ending_clean = ending.rstrip("。")
            last_para = paragraphs[-1].rstrip("。")
            if last_para == ending_clean:
                paragraphs.pop()
            elif last_para.endswith(ending_clean):
                paragraphs[-1] = last_para[:last_para.rfind(ending_clean)].rstrip("。")

        # Step 5: 解析模板
        template = self.template_mgr.resolve_template(
            article.get("template_name")
        )

        # Step 6: 渲染HTML
        html = self.template_mgr.render(
            template=template,
            title=title,
            summary=summary,
            intro=intro,
            body_paragraphs=paragraphs,
            images=render_images,
            ending=ending,
        )

        # Step 7: 最终检查（无外部CSS/无script/无iframe）
        html = self._sanitize_html(html)

        # 组装结果
        result = {
            "is_success": True,
            "title": title,
            "summary": summary,
            "html_content": html,
            "word_count": len(content_text.replace(" ", "")),
            "paragraph_count": len(paragraphs),
            "image_count": len(valid_images),
            "cover_only_mode": cover_only_mode,
            "consistency_score": consistency.get("consistency_score", 0),
            "template_used": template.get("name") if template else "system_default",
            "position": article.get("position", "unknown"),
            # 传递给发布Agent
            "valid_images": valid_images,
            "cover_image": cover_image,
            "inline_images": render_images,
        }

        return result

    def _extract_intro(self, text: str) -> str:
        """提取导语段（正文前1-2句）"""
        # 按句号拆分
        sentences = re.split(r'[。！？.!?]', text.strip())
        sentences = [s.strip() for s in sentences if s.strip()]

        if not sentences:
            return ""

        # 取前1-2句作为导语
        intro = sentences[0]
        if len(sentences) >= 2 and len(intro) < 60:
            intro += "。" + sentences[1]

        return intro + "。"

    def _extract_ending(self, text: str) -> str:
        """提取/生成结尾段（有余味）"""
        sentences = re.split(r'[。！？.!?]', text.strip())
        sentences = [s.strip() for s in sentences if s.strip()]

        if not sentences:
            return ""

        # 取最后一句作为结尾
        return sentences[-1] + "。"

    def _sanitize_html(self, html: str) -> str:
        """清理HTML，确保兼容微信公众号"""
        # 移除 <script> 标签
        html = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
        # 移除 <iframe> 标签
        html = re.sub(r'<iframe[^>]*>.*?</iframe>', '', html, flags=re.DOTALL | re.IGNORECASE)
        # 移除外部 CSS link
        html = re.sub(r'<link[^>]*rel=["\']stylesheet["\'][^>]*>', '', html, flags=re.IGNORECASE)
        # 默认移除所有图片说明，避免文件名、URL、Tavily 描述等技术文字
        html = re.sub(
            r'<figcaption[^>]*>.*?</figcaption>',
            '',
            html,
            flags=re.DOTALL | re.IGNORECASE,
        )
        html = re.sub(
            r'<p[^>]*style=["\'][^"\']*(?:font-size\s*:\s*13px|'
            r'color\s*:\s*#999999)[^"\']*["\'][^>]*>.*?</p>',
            '',
            html,
            flags=re.DOTALL | re.IGNORECASE,
        )
        return html

    async def _save_html(self, article: Dict):
        """保存排版后HTML到本地"""
        articles_dir = Path(self.get_config("project_root", ".")) / "data" / "articles"
        articles_dir.mkdir(parents=True, exist_ok=True)

        timestamp = __import__("datetime").datetime.now().strftime("%Y%m%d_%H%M%S")
        position = article.get("position", "unknown")
        safe_title = "".join(
            c for c in article.get("title", "")[:30] if c.isalnum() or c in "_ "
        )

        filepath = articles_dir / f"formatted_{timestamp}_{position}_{safe_title}.html"

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(f"<!-- 标题: {article['title']} -->\n")
            f.write(f"<!-- 位置: {position} -->\n")
            f.write(f"<!-- 字数: {article['word_count']} -->\n")
            f.write(f"<!-- 模板: {article.get('template_used', '?')} -->\n")
            f.write(f"<!-- 图文一致性: {article.get('consistency_score', 0):.0f} -->\n\n")
            f.write(article.get("html_content", ""))

        logger.info(f"[排版Agent] HTML已保存: {filepath}")
