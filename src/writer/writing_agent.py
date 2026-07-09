"""② 写作 Agent - 韩国爱豆状态观察文风 + 严格禁止规则 + Tavily源文改写"""

import asyncio
import html
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from src.base_agent import BaseAgent, AgentStatus, AgentResult
from src.logger import logger


class WritingAgent(BaseAgent):
    """
    写作 Agent 职责：
    1. 接收选题信息（含Tavily源文章和图片）
    2. 基于源文章内容用LLM改写
    3. 严格遵循禁止编造规则和禁止口吻
    4. 文章200-800字，短段落，适合公众号
    5. "爱豆状态观察"文风，不是饭圈尖叫文
    6. 将源文章配图传递下去（确保图文绑定）

    输出: {
        "title": "文章标题",
        "summary": "文章摘要(≤120字)",
        "content_text": "纯文本正文(200-800字)",
        "content_html": "排版后HTML(含图片占位)",
        "source_articles": [...],
        "tavily_images": [...],
        "word_count": 字数,
        "anti_ai_score": 反AI分数,
    }
    """

    name = "writer_agent"
    display_name = "② 写作 Agent (爱豆状态观察文风)"

    def __init__(self, config: Optional[Dict] = None):
        super().__init__(config)
        self.llm_client = self._init_llm()
        self.http_client = None

    def _init_llm(self):
        from openai import AsyncOpenAI
        return AsyncOpenAI(
            api_key=self.get_config("llm.api_key"),
            base_url=self.get_config("llm.base_url"),
        )

    async def _get_http(self):
        if self.http_client is None:
            self.http_client = httpx.AsyncClient(timeout=30.0)
        return self.http_client

    async def execute(self, context: Dict) -> AgentResult:
        """
        对选题中的每篇文章执行写作（支持批量，但通常2篇）

        流程:
        1. 从 context 读取 selected_topics
        2. 对每篇选题执行写作
        3. 严格检查禁止词和格式
        """
        selected = context.get("selected_topics", [])
        if not selected:
            return AgentResult(
                status=AgentStatus.FAILED,
                agent_name=self.name,
                error="Context中未找到 selected_topics",
            )

        logger.info(f"[写作Agent] 开始写作 {len(selected)} 篇文章...")

        results = []
        for i, topic in enumerate(selected):
            logger.info(
                f"[写作Agent] 文章 {i+1}/{len(selected)}: '{topic.get('title','?')[:40]}'"
            )

            result = await self._write_single(topic, context)
            results.append(result)

            if not result.get("is_success"):
                logger.warning(
                    f"[写作Agent] 文章 {i+1} 写作失败: {result.get('error', '?')}"
                )

        # 合并结果（多篇文章的源文章和图片合并）
        all_source_articles = []
        all_images = []
        written_articles = []

        for r in results:
            if r.get("is_success"):
                written_articles.append(r)
                all_source_articles.extend(r.get("source_articles", []))
                all_images.extend(r.get("tavily_images", []))

        if not written_articles:
            return AgentResult(
                status=AgentStatus.FAILED,
                agent_name=self.name,
                error="所有文章写作均失败",
            )

        # 保存草稿
        for article in written_articles:
            await self._save_draft(article)

        logger.info(
            f"[写作Agent] ✅ 完成! 成功 {len(written_articles)}/{len(selected)} 篇"
        )

        return AgentResult(
            status=AgentStatus.SUCCESS,
            agent_name=self.name,
            output={
                "written_articles": written_articles,
                "source_articles": all_source_articles,
                "tavily_images": all_images,
                "success_count": len(written_articles),
                "total_count": len(selected),
            },
        )

    async def _write_single(self, topic: Dict, context: Dict) -> Dict:
        """对单篇选题执行写作"""
        # 获取源文章和图片
        source_articles = self._get_source_articles(topic)
        source_articles = await self._enrich_source_articles(source_articles)
        fact_points = self._extract_fact_points(source_articles)
        short_news_mode = (
            self.get_config(
                "writing.allow_short_news_when_facts_limited", True
            )
            and len(fact_points) < 5
        )
        topic["extracted_facts"] = fact_points
        topic["short_news_mode"] = short_news_mode
        aliases, alias_contexts = self._detect_source_entities_with_context(
            source_articles
        )
        logger.info(
            f"[写作Agent] 写作前事实检查 | "
            f"original_title={topic.get('original_title') or topic.get('title', '')} | "
            f"source_url={topic.get('source_url') or topic.get('url', '')} | "
            f"source_language={topic.get('source_language', 'unknown')} | "
            f"extracted_facts_count={len(fact_points)} | "
            f"aliases_detected={aliases} | "
            f"aliases_source={list(alias_contexts)} | "
            f"alias_context={alias_contexts} | "
            f"short_news_mode={short_news_mode} | "
            f"允许写作={bool(fact_points)}"
        )
        if not fact_points:
            return {"is_success": False, "error": "源标题/摘要不足，无法提取写作事实"}
        tavily_images = topic.get("_tavily_images", [])

        # 构建写作提示词（严格遵循用户规则）
        prompt = self._build_writing_prompt(
            topic, source_articles, tavily_images,
            fact_points=fact_points, short_news_mode=short_news_mode,
        )

        # 调用 LLM
        try:
            response = await self.llm_client.chat.completions.create(
                model=self.get_config("llm.model", "deepseek-v4-flash"),
                messages=[
                    {
                        "role": "system",
                        "content": self._get_system_prompt(),
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=self.get_config("llm.temperature.writing", 0.7),
                max_tokens=self.get_config("llm.max_tokens", 4096),
            )

            content = response.choices[0].message.content.strip()

            # 清理 ```markdown 包裹
            if content.startswith("```"):
                lines = content.split("\n")
                content = "\n".join(
                    lines[1:-1] if lines[-1].strip() == "```" else lines[1:]
                )

            # 解析输出（期望JSON格式）
            parsed = self._parse_output(content)

            if not parsed:
                parsed = await self._retry_with_stricter_prompt(
                    topic, source_articles, tavily_images,
                    ["LLM输出解析失败"], short_news_mode=short_news_mode,
                )
            if not parsed:
                return self.build_safe_fallback_article(
                    topic, fact_points, source_articles, tavily_images
                )

            # 严格检查
            check_result = self._strict_check(parsed, short_news_mode)
            fidelity_issues = self._check_source_fidelity(
                parsed, source_articles
            )
            if fidelity_issues:
                severe = [
                    issue for issue in fidelity_issues
                    if issue.startswith("严重事实")
                ]
                soft = [issue for issue in fidelity_issues if issue not in severe]
                check_result["hard_issues"].extend(severe)
                check_result["warnings"].extend(soft)
                check_result["issues"].extend(fidelity_issues)
                check_result["passed"] = not check_result["hard_issues"]
            if check_result["warnings"]:
                logger.warning(
                    f"[写作Agent] production soft warnings: "
                    f"{check_result['warnings']}"
                )
            if not check_result["passed"]:
                # 尝试修复或直接失败
                logger.warning(
                    f"[写作Agent] 严格检查未通过: {check_result['issues']} | "
                    f"生成稿片段={parsed.get('content_text', '')[:180]}"
                )
                # 重新生成（简化版）
                parsed = await self._retry_with_stricter_prompt(
                    topic, source_articles, tavily_images, check_result["issues"],
                    short_news_mode=short_news_mode,
                )
                if parsed:
                    check_result = self._strict_check(parsed, short_news_mode)
                    fidelity_issues = self._check_source_fidelity(
                        parsed, source_articles
                    )
                    if fidelity_issues:
                        severe = [
                            issue for issue in fidelity_issues
                            if issue.startswith("严重事实")
                        ]
                        soft = [
                            issue for issue in fidelity_issues
                            if issue not in severe
                        ]
                        check_result["hard_issues"].extend(severe)
                        check_result["warnings"].extend(soft)
                        check_result["issues"].extend(fidelity_issues)
                        check_result["passed"] = not check_result["hard_issues"]

            if not check_result["passed"]:
                logger.error(
                    f"[写作Agent] 两次生成均失败 | "
                    f"原因={check_result['issues']} | "
                    f"重试稿片段={parsed.get('content_text', '')[:240]}"
                )
                return self.build_safe_fallback_article(
                    topic, fact_points, source_articles, tavily_images,
                    fallback_reason=check_result["hard_issues"],
                )

            # 组装结果
            word_count = len(parsed.get("content_text", "").replace(" ", ""))

            result = {
                "is_success": True,
                "title": parsed.get("title", topic.get("title", "")),
                "summary": parsed.get("summary", ""),
                "content_text": parsed.get("content_text", ""),
                "content_html": parsed.get("content_html", ""),
                "word_count": word_count,
                "source_articles": source_articles,
                "tavily_images": tavily_images,
                "topic_info": topic,
                "position": topic.get("position", "unknown"),
                "extracted_facts": fact_points,
                "short_news_mode": short_news_mode,
            }

            return result

        except Exception as e:
            logger.error(f"[写作Agent] LLM调用异常: {e}")
            fallback = self.build_safe_fallback_article(
                topic, fact_points, source_articles, tavily_images,
                fallback_reason=[f"LLM调用异常: {e}"],
            )
            if fallback.get("is_success"):
                return fallback
            return {"is_success": False, "error": str(e)}

    def _get_source_articles(self, topic: Dict) -> List[Dict]:
        """获取与当前选题相关的源文章列表"""
        # 当前 topic 本身就是一个源文章
        return [topic]

    async def _enrich_source_articles(
        self, source_articles: List[Dict]
    ) -> List[Dict]:
        """最终候选写作前尝试补充 og/meta 与正文，不引入额外 Tavily 调用。"""
        enriched = []
        for original in source_articles:
            article = dict(original)
            url = article.get("source_url") or article.get("url")
            existing = article.get("content") or article.get("summary") or ""
            if url and len(existing.strip()) < 300:
                try:
                    response = await (await self._get_http()).get(
                        url, follow_redirects=True,
                        headers={"User-Agent": "Mozilla/5.0"},
                    )
                    response.raise_for_status()
                    page = response.text[:500000]
                    og_title = self._meta_content(page, "og:title")
                    description = (
                        self._meta_content(page, "og:description")
                        or self._meta_content(page, "description")
                    )
                    article_match = re.search(
                        r"<article\b[^>]*>(.*?)</article>", page,
                        flags=re.IGNORECASE | re.DOTALL,
                    )
                    article_html = article_match.group(1) if article_match else ""
                    body = " ".join(
                        html.unescape(re.sub(r"<[^>]+>", " ", block))
                        for block in re.findall(
                            r"<p\b[^>]*>(.*?)</p>", article_html,
                            flags=re.IGNORECASE | re.DOTALL,
                        )[:20]
                    )
                    body = re.sub(r"\s+", " ", body).strip()
                    article["og_title"] = og_title
                    article["meta_description"] = description
                    if body and len(body) >= 80:
                        article["article_body"] = body[:6000]
                except Exception as exc:
                    logger.info(
                        f"[写作Agent] 正文抓取不可用，回退 title/snippet: "
                        f"{url} | {exc}"
                    )
            enriched.append(article)
        return enriched

    @staticmethod
    def _meta_content(page: str, key: str) -> str:
        key_pattern = re.escape(key)
        patterns = [
            rf'<meta[^>]+(?:property|name)=["\']{key_pattern}["\'][^>]+content=["\'](.*?)["\']',
            rf'<meta[^>]+content=["\'](.*?)["\'][^>]+(?:property|name)=["\']{key_pattern}["\']',
        ]
        for pattern in patterns:
            match = re.search(pattern, page, re.IGNORECASE | re.DOTALL)
            if match:
                return html.unescape(match.group(1)).strip()
        return ""

    def _get_system_prompt(self) -> str:
        """获取系统提示词（严格写作规则）"""
        return """你是一位中文韩娱资讯公众号编辑。请提炼轻快、自然、适合手机阅读的韩娱快讯风格，不模仿或复制任何具体账号的原文和固定句式。

【最重要规则】所有输出必须是简体中文。源文章可能是韩语、英语或其他语言，你必须将所有内容翻译为流畅的简体中文。标题、摘要、正文中不允许出现任何韩语字符（가-힣）或英语句子。韩国人名、团体名应使用中文译名或通用名（如"BTS""BLACKPINK"等国际通用名可保留英文，但正文叙述必须中文）。

写作规则（必须严格遵守）：
1. 禁止编造图片细节：如果输入没有明确图片描述，禁止写任何具体画面
2. 禁止自行添加：服装、动作、道具、场景、光线、表情、成员互动
3. 禁止写"照片里""那张""第一张""最后一张""六宫格"等具体图片描述
4. 禁止写未经输入提供的信息（西瓜、白T、牛仔裤等）
5. 禁止使用饭圈口吻：姐妹们、啊啊啊、呜呜呜、美到失语、绝了、封神、鲨疯了等
6. 禁止输出 hashtag、emoji、硬性互动CTA
7. 只输出正文，不要重复标题
8. 不要输出 Markdown 标题符号 #
9. 正文目标约400字、理想范围350-500字；原文事实少时允许160-300字短讯，忠实优先，绝不为凑字数新增内容
10. 多用短句和自然分段，每段约25-80字，适合手机阅读
11. 先用一句话点明“谁、发生了什么”，再写公开可确认的看点和讨论点
12. 语气像中文韩娱快讯：轻快、克制、不尖叫、不造谣；不要强写粉丝或网友反应
13. 避免正式新闻稿和AI总结腔：不用“据悉、此外、值得注意的是、引发广泛关注、具有重要意义、展现国际影响力、从行业角度来看、文化输出”
14. 标题把艺人名放前面，信息点明确、简短自然，不要机器翻译感和夸张标题党
15. 涉及恋情、争议、法律回应时只复述来源已公开事实，不扩写私人细节，不把猜测写成事实
15.1 禁止“被指与女友调情、真实颜值、隐藏颜值、借题发挥、具体细节我们不再展开、恋情实锤、暧昧、翻车、疑似塌房、网友怒批”等刺激或暗示性表达
15.2 改用“相关片段引发讨论、网友看法不一、粉丝呼吁别过度解读、评论区观点不一”等中性表述
16. 结尾轻轻收束，可自然提到后续动态或留下一个克制的问题，不上价值
17. 韩国人名统一用中文译名（如"宋智孝""苏志燮"），团体名可用国际通用名
18. 代词不确定时反复使用艺人名或团体名，避免“他/她/他们”混乱
19. 涉及粉丝、健康、疾病、公益或特殊标签时保持克制，不消费标签，不把粉丝身份写成卖点

输出格式：严格JSON，包含 title, summary, content_text, content_html 字段。"""

    def _build_writing_prompt(
        self, topic: Dict, source_articles: List[Dict], tavily_images: List[Dict],
        fact_points: Optional[List[str]] = None,
        short_news_mode: bool = False,
    ) -> str:
        """构建写作提示词"""
        # 构建源文章参考
        source_material = self._build_source_material(source_articles)
        fact_points = fact_points or self._extract_fact_points(source_articles)

        # 构建图片信息（不含具体画面描述，只提供安全信息）
        image_info = self._build_image_info(tavily_images)

        # 获取写作规则
        banned = self.get_config("writer_agent.banned_phrases", [])
        safe = self.get_config("writer_agent.safe_expressions", [])
        rules = self.get_config("writer_agent.format_rules", {})

        prompt = f"""请为以下选题撰写一篇中文韩娱资讯公众号短文。

【语言要求】所有输出必须是简体中文。源文章可能是韩语或英语，你必须完全翻译为中文。标题、摘要、正文中不允许保留韩语原文。

## 选题信息
- 标题/主题: {topic.get("title", "")}
- 来源: {topic.get("source_name", "")}
- 评分: {topic.get("scores", {}).get("total_score", "?")}

## 源文章内容参考（可能为韩语/英语，请翻译为中文后使用）
{source_material}

## 原文事实清单（只能基于这些事实展开）
{chr(10).join(f"- {fact}" for fact in fact_points)}

## 可用图片信息（仅用于判断图片数量，不要写具体画面）
{image_info}

## 写作要求
1. **语言**: 全文简体中文，韩语/英语源文必须翻译，韩国人名用中文译名
2. **字数**: 目标约400字，理想350-500字。{"当前为短讯模式：事实较少，允许160-300字，忠实完整即可，绝不为凑字数扩写" if short_news_mode else "当前事实较充分，优先写到350-500字"}；绝对不能用原文没有的信息补字数
3. **忠实度**: 人名、团体、公司、时间、地点、事件性质、官方回应和争议边界必须与原文保持一致；事实内容保持85%-90%以上一致
4. **整理范围**: 只做轻度中文资讯化整理，可调整中文表达、段落顺序和阅读节奏；不逐句翻译，也不新增事实
5. **文风**: 轻快自然的韩娱快讯。少正式新闻腔，先说谁发生了什么，再写公开看点和讨论
6. **段落**: 多用短句，每段25-80字，手机阅读时不要出现长句堆叠
7. **禁止口吻**: {', '.join(banned[:8])} 等饭圈表达
8. **安全表达**: 可用 {', '.join(safe[:5])}
9. **格式**: {"不输出Markdown标题符号" if rules.get("no_markdown_headings") else ""} {"不输出hashtag" if rules.get("no_hashtags") else ""}
10. **事实边界**: 网友/粉丝反应不是固定段落；原文没有时完全不要写，原文有时才忠实保留。文章结构按事实决定，不强制四段
11. **禁止宏观发挥**: 不写市场定位、公司格局、世代交替、行业趋势、全球影响力等原文没有的判断
12. **争议边界**: 不把猜测写成事实，不扩大争议，不使用刺激性定性
13. **图片边界**: 未确认的服装、动作、表情、背景、构图一律不写
14. **标题**: 艺人名放前面，信息点明确、自然简短，不要机器翻译腔
15. **结尾**: 轻轻收束，不上价值，不使用强制点赞关注类互动
16. **韩娱资讯号语感**: 标题和开头要像韩娱资讯号快讯，信息点前置，短句自然；不要学术化、营销化或硬煽情
17. **代词与敏感表述**: 代词不确定时用艺人名/团体名；涉及粉丝、健康或特殊标签时只写公开事实，语气克制

## 输出格式（严格JSON）
```json
{{
  "title": "文章标题（简体中文，不含饭圈词汇）",
  "summary": "摘要（简体中文，≤120字）",
  "content_text": "纯文本正文（简体中文，{"短讯模式160-300字" if short_news_mode else "目标350-500字"}，忠实优先）",
  "content_html": "HTML正文（含<img>标签占位，用{{IMAGE_N}}替换实际图片位置）"
}}
```

请直接输出JSON，不要额外解释。"""

        return prompt

    def _build_source_material(self, source_articles: List[Dict]) -> str:
        """构建源文章参考材料"""
        if not source_articles:
            return "（无源文章内容，请基于选题标题自行创作状态观察文章）"

        parts = []
        for i, article in enumerate(source_articles[:3]):
            title = article.get("title", "未知标题")
            content = (
                article.get("article_body")
                or article.get("content")
                or article.get("raw_content")
                or article.get("meta_description")
                or article.get("summary")
                or ""
            )
            content_preview = content[:600] if content else "（无内容）"

            parts.append(
                f"### 源文章 {i+1}: {title}\n"
                f"内容参考:\n{content_preview}\n"
            )

        return "\n---\n".join(parts)

    def _extract_fact_points(self, source_articles: List[Dict]) -> List[str]:
        """从原题和原文摘取5-8个事实片段，作为写作边界。"""
        points = []
        for article in source_articles[:3]:
            title = (
                article.get("og_title")
                or article.get("original_title")
                or article.get("title")
                or ""
            ).strip()
            if title:
                points.append(f"原文标题：{title}")
            content = (
                article.get("article_body")
                or article.get("content")
                or article.get("raw_content")
                or article.get("meta_description")
                or article.get("summary")
                or ""
            )
            for sentence in re.split(r"(?<=[。！？.!?])\s+|\n+", content):
                sentence = sentence.strip()
                if 20 <= len(sentence) <= 240:
                    points.append(sentence)
                if len(points) >= 8:
                    break
            if len(points) >= 8:
                break
        return points[:8]

    @staticmethod
    def _source_text(source_articles: List[Dict]) -> str:
        return " ".join(
            str(article.get(key) or "")
            for article in source_articles
            for key in (
                "og_title", "original_title", "title", "meta_description",
                "summary", "content", "raw_content", "article_body",
            )
        )

    @staticmethod
    def _detect_source_entities(text: str) -> set:
        from src.topic.scoring import ScoringSystem
        return ScoringSystem.detect_artist_entities(text)

    @staticmethod
    def _detect_source_entities_with_context(source_articles: List[Dict]):
        from src.topic.scoring import ScoringSystem
        fields = {
            "title": ("og_title", "original_title", "title"),
            "snippet": ("summary", "content"),
            "meta_description": ("meta_description",),
            "article_body_cleaned": ("article_body",),
        }
        entities = set()
        contexts = {}
        for label, keys in fields.items():
            text = " ".join(
                str(article.get(key) or "")
                for article in source_articles
                for key in keys
            ).strip()
            if not text:
                continue
            hits = ScoringSystem.detect_artist_entities(text)
            if hits:
                entities.update(hits)
                contexts[label] = {
                    "aliases": sorted(hits),
                    "context": text[:180],
                }
        return sorted(entities), contexts

    def _check_source_fidelity(
        self, parsed: Dict, source_articles: List[Dict]
    ) -> List[str]:
        """阻止新增艺人、公司回应、网友反应和宏观行业判断。"""
        issues = []
        output = " ".join([
            parsed.get("title", ""),
            parsed.get("summary", ""),
            parsed.get("content_text", ""),
        ])
        source = self._source_text(source_articles)
        output_lower = output.lower()
        source_lower = source.lower()

        macro_phrases = [
            "市场定位", "延续巨头影响力", "世代交替", "转型期阵痛",
            "四大公司接班人", "从行业角度来看", "文化输出",
            "全球影响力持续扩大", "行业格局", "产业趋势",
        ]
        for phrase in macro_phrases:
            if phrase in output:
                issues.append(f"新增AI宏观判断: '{phrase}'")

        reaction_terms = [
            "网友", "粉丝", "评论区", "netizen", "fans react",
            "네티즌", "팬", "관객", "환호", "응원",
        ]
        if any(term in output_lower for term in reaction_terms) and not any(
            term in source_lower for term in reaction_terms
        ):
            issues.append("原文未提供网友/粉丝反应，禁止自行补充")

        company_aliases = {
            "hybe": ["hybe", "하이브"],
            "bighit": ["bighit", "빅히트"],
            "ador": ["ador", "어도어"],
            "sm entertainment": ["sm entertainment", "sm엔터테인먼트"],
            "jyp entertainment": ["jyp entertainment", "jyp엔터테인먼트"],
            "yg entertainment": ["yg entertainment", "yg엔터테인먼트"],
            "starship": ["starship", "스타쉽"],
            "source music": ["source music", "쏘스뮤직"],
        }
        for company, aliases in company_aliases.items():
            if (
                any(alias in output_lower for alias in aliases)
                and not any(alias in source_lower for alias in aliases)
            ):
                issues.append(f"公司别名待核对: {company}")

        try:
            from src.topic.scoring import ScoringSystem
            source_people = ScoringSystem.detect_artist_entities(source)
            output_people = ScoringSystem.detect_artist_entities(output)
            for person in sorted(output_people - source_people):
                issues.append(f"艺人别名待核对: {person}")
        except Exception:
            pass

        if (
            any(term in output for term in ["确认恋情", "证实恋情", "恋情属实"])
            and not any(term in source_lower for term in [
                "confirmed relationship", "确认恋情", "证实恋情", "officially confirmed"
            ])
        ):
            issues.append("严重事实：原文未确认恋情，禁止把猜测写成事实")
        return issues

    def _build_image_info(self, tavily_images: List[Dict]) -> str:
        """构建图片信息（不含具体画面描述）"""
        if not tavily_images:
            return "（无配图）"

        count = len(tavily_images)
        lines = [f"共 {count} 张配图可用"]

        for i, img in enumerate(tavily_images):
            desc = img.get("description", "")
            if desc:
                # 只提供安全描述，不写具体画面
                safe_desc = desc[:50] if len(desc) <= 50 else desc[:50] + "..."
                lines.append(f"  图{i+1}: {safe_desc}")
            else:
                lines.append(f"  图{i+1}: （无描述）")

        lines.append("\n注意：正文中不要写具体图片细节，除非图片描述明确提供了。")
        return "\n".join(lines)

    def _parse_output(self, content: str) -> Optional[Dict]:
        """解析LLM输出"""
        try:
            # 尝试直接解析
            if content.strip().startswith("{"):
                return json.loads(content)

            # 尝试提取 ```json ... ``` 中的内容
            if "```json" in content:
                json_str = content.split("```json")[1].split("```")[0].strip()
                return json.loads(json_str)
            elif "```" in content:
                json_str = content.split("```")[1].split("```")[0].strip()
                return json.loads(json_str)

        except (json.JSONDecodeError, IndexError) as e:
            logger.warning(f"[写作Agent] JSON解析失败: {e}, 原始: {content[:200]}")

        return None

    def _strict_check(
        self, parsed: Dict, short_news_mode: bool = False
    ) -> Dict:
        """生产校验：少量 hard fail，其余仅记录 soft warning。"""
        hard_issues = []
        warnings = []

        content = (parsed.get("content_text") or "").strip()
        title = (parsed.get("title") or "").strip()
        summary = parsed.get("summary", "")
        title_has_chinese = bool(re.search(r"[\u4e00-\u9fff]", title))
        if not title:
            hard_issues.append("标题为空")
        if not content:
            hard_issues.append("正文为空")

        chinese_count = len(re.findall(r"[\u4e00-\u9fff]", content))
        if content and chinese_count < 80:
            hard_issues.append(f"中文正文过短({chinese_count} < 80)")
        requires_repair = (
            (bool(content) and 80 <= chinese_count < 100)
            or (bool(title) and not title_has_chinese)
        )
        if title and not title_has_chinese:
            warnings.append("标题缺少中文信息")
        if requires_repair:
            warnings.append(f"中文正文不足生产下限({chinese_count} < 100)")

        # 韩文或英文专有名词只提示，不阻断生产。
        korean_pattern = re.compile(r'[\uac00-\ud7af\u1100-\u11ff\u3130-\u318f]')
        if korean_pattern.search(title):
            warnings.append("标题含少量韩语专有名词")
        if korean_pattern.search(summary):
            warnings.append("摘要含少量韩语专有名词")
        if korean_pattern.search(content):
            korean_fragments = korean_pattern.findall(content)
            warnings.append(f"正文含韩语专有名词({len(korean_fragments)}处)")

        char_count = len(content.replace(" ", "").replace("\n", ""))
        if 80 <= chinese_count < 160:
            warnings.append(f"生产短文模式({chinese_count}个中文字符)")
        if char_count > 1200:
            warnings.append(f"正文偏长({char_count})")

        banned = self.get_config("writer_agent.banned_phrases", [])
        for phrase in banned:
            if phrase in content or phrase in title:
                warnings.append(f"含不推荐口吻: '{phrase}'")

        if re.search(r'#\w', content):
            warnings.append("含hashtag")
        if re.search(r'[\U0001F000-\U0001FFFF\U00002600-\U000027FF]', content):
            warnings.append("含emoji")
        if re.search(r'^#+\s', content, re.MULTILINE):
            warnings.append("含Markdown标题符号")
        if title and title in content:
            warnings.append("标题在正文重复")

        forbidden_patterns = [
            r'照片[里中第那]',
            r'那张',
            r'第一张',
            r'最后一张',
            r'六宫格',
            r'西瓜',
            r'白T',
            r'棒球帽',
            r'比耶',
            r'合照',
        ]
        for pattern in forbidden_patterns:
            if re.search(pattern, content):
                warnings.append(f"疑似图片细节: 匹配'{pattern}'")

        severe_patterns = [
            "确认死亡", "已经去世", "涉嫌违法", "被判刑", "判处",
            "确认恋情", "证实恋情", "恋情属实", "吸毒", "色情",
            "仇恨言论", "政治立场", "非法交易", "被指与女友调情",
            "恋情实锤", "疑似塌房",
        ]
        for phrase in severe_patterns:
            if phrase in content or phrase in title:
                hard_issues.append(f"高风险事实/表达: '{phrase}'")

        issues = hard_issues + warnings
        return {
            "passed": len(hard_issues) == 0 and not requires_repair,
            "hard_issues": hard_issues,
            "warnings": warnings,
            "requires_repair": requires_repair,
            "issues": issues,
        }

    async def _retry_with_stricter_prompt(
        self, topic, source_articles, tavily_images, issues,
        short_news_mode: bool = False,
    ) -> Optional[Dict]:
        """使用更严格的提示词重试"""
        logger.info(f"[写作Agent] 重试生成，问题: {issues}")

        stricter_prompt = f"""上次生成的内容有以下问题: {', '.join(issues)}

请重新生成，特别注意:
1. 所有内容必须是简体中文，韩语/英语必须完全翻译
2. 绝对不要出现上述问题
3. {"当前是事实有限的短讯模式，允许160-300字，不为凑400字补写" if short_news_mode else "事实点不少于5个，必须逐点展开到300-500字，低于250字不合格"}；忠实优先
4. 不要写任何具体图片画面细节
5. 不要使用饭圈口吻
6. 韩国人名用中文译名
7. 争议内容使用中性表达，不写女友、调情、真实颜值、借题发挥、恋情实锤、暧昧、翻车或疑似塌房
8. 严格依据以下原文事实，不新增人物、公司回应、网友反应或行业判断：
{chr(10).join(f"- {fact}" for fact in self._extract_fact_points(source_articles))}
9. 逐条利用上述事实扩展中文表达；禁止写粉丝热议、网友期待、评论区热闹，除非事实清单明确包含

选题: {topic.get("title", "")}
源文章参考: {self._build_source_material(source_articles)[:500]}

请输出严格JSON格式，所有字段均为简体中文。"""

        try:
            response = await self.llm_client.chat.completions.create(
                model=self.get_config("llm.model", "deepseek-v4-flash"),
                messages=[
                    {"role": "system", "content": self._get_system_prompt()},
                    {"role": "user", "content": stricter_prompt},
                ],
                temperature=0.5,  # 降低温度
                max_tokens=self.get_config("llm.max_tokens", 4096),
            )

            content = response.choices[0].message.content.strip()
            return self._parse_output(content)

        except Exception as e:
            logger.error(f"[写作Agent] 重试失败: {e}")
            return None

    def build_safe_fallback_article(
        self,
        topic: Dict,
        fact_points: Optional[List[str]] = None,
        source_articles: Optional[List[Dict]] = None,
        tavily_images: Optional[List[Dict]] = None,
        fallback_reason: Optional[List[str]] = None,
    ) -> Dict:
        """基于已提取事实生成保守可发布稿，不追求风格化。"""
        facts = fact_points or topic.get("extracted_facts") or self._extract_fact_points(
            source_articles or [topic]
        )
        if not facts:
            return {"is_success": False, "error": "safe fallback 缺少事实"}

        source_title = (
            topic.get("original_title") or topic.get("title") or ""
        ).strip()
        group_match = re.search(
            r"ITZY|TWS|NewJeans|ADOR|ENHYPEN|IVE|Stray Kids|"
            r"BLACKPINK|BTS|aespa|KISS OF LIFE",
            source_title,
            re.IGNORECASE,
        )
        subject = group_match.group(0) if group_match else "韩国偶像"
        title = f"{subject}最新活动动态"
        facts_text = "；".join(
            re.sub(r"\s+", " ", fact).strip()[:180]
            for fact in facts[:5]
            if fact.strip()
        )
        content = (
            f"根据现有公开资料，本次消息围绕{subject}的最新活动展开。"
            f"原文已经确认的信息包括：{facts_text}。"
            "本文仅对现有标题、摘要和公开事实进行中文整理，没有补充未经来源确认的"
            "恋情、法律结论、金额、日期或公司回应。相关活动的具体时间、地点和后续"
            "安排，均以原报道及官方渠道后续公开的信息为准。"
        )
        # 保证生产稿达到最低可用长度，但不新增事件事实。
        if len(re.findall(r"[\u4e00-\u9fff]", content)) < 100:
            content += (
                "目前可确认内容以原文列出的活动进展为限，后续如有新的正式安排，"
                "将以艺人、组合或所属公司的官方发布为准。"
            )
        check = self._strict_check(
            {"title": title, "summary": "", "content_text": content},
            short_news_mode=True,
        )
        if not check["passed"]:
            return {
                "is_success": False,
                "error": f"safe fallback hard fail: {check['hard_issues']}",
            }
        logger.warning(
            f"[写作Agent] 使用 production safe fallback | "
            f"原因={fallback_reason or []} | 字数={len(content)}"
        )
        images = tavily_images if tavily_images is not None else topic.get(
            "_tavily_images", []
        )
        return {
            "is_success": True,
            "title": title,
            "summary": f"{subject}最新公开动态的事实整理。",
            "content_text": content,
            "content_html": "".join(
                f"<p>{paragraph}</p>"
                for paragraph in re.split(r"(?<=[。])", content)
                if paragraph.strip()
            ),
            "word_count": len(content.replace(" ", "")),
            "source_articles": source_articles or [topic],
            "tavily_images": images,
            "topic_info": topic,
            "position": topic.get("position", "unknown"),
            "extracted_facts": facts,
            "short_news_mode": True,
            "production_fallback": True,
            "soft_warnings": check["warnings"],
        }

    async def _save_draft(self, article: Dict):
        """保存文章草稿到本地"""
        articles_dir = Path(self.get_config("project_root", ".")) / "data" / "articles"
        articles_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        position = article.get("position", "unknown")
        safe_title = "".join(
            c for c in article.get("title", "")[:30] if c.isalnum() or c in "_ "
        )

        draft_file = articles_dir / f"draft_{timestamp}_{position}_{safe_title}.txt"

        with open(draft_file, "w", encoding="utf-8") as f:
            f.write(f"标题: {article['title']}\n")
            f.write(f"位置: {position}\n")
            f.write(f"字数: {article['word_count']}\n")
            f.write(f"摘要: {article.get('summary', '')}\n")
            f.write("\n---\n\n")
            f.write(article.get("content_text", ""))
            f.write("\n")

        logger.info(f"[写作Agent] 草稿已保存: {draft_file}")

    async def cleanup(self):
        if self.http_client:
            await self.http_client.aclose()
