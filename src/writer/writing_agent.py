"""② 写作 Agent - 韩国爱豆状态观察文风 + 严格禁止规则 + Tavily源文改写"""

import asyncio
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
        tavily_images = topic.get("_tavily_images", [])

        # 构建写作提示词（严格遵循用户规则）
        prompt = self._build_writing_prompt(topic, source_articles, tavily_images)

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
                return {"is_success": False, "error": "LLM输出解析失败"}

            # 严格检查
            check_result = self._strict_check(parsed)
            if not check_result["passed"]:
                # 尝试修复或直接失败
                logger.warning(
                    f"[写作Agent] 严格检查未通过: {check_result['issues']}"
                )
                # 重新生成（简化版）
                parsed = await self._retry_with_stricter_prompt(
                    topic, source_articles, tavily_images, check_result["issues"]
                )
                if parsed:
                    check_result = self._strict_check(parsed)

            if not check_result["passed"]:
                return {
                    "is_success": False,
                    "error": f"严格检查未通过: {check_result['issues']}",
                }

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
            }

            return result

        except Exception as e:
            logger.error(f"[写作Agent] LLM调用异常: {e}")
            return {"is_success": False, "error": str(e)}

    def _get_source_articles(self, topic: Dict) -> List[Dict]:
        """获取与当前选题相关的源文章列表"""
        # 当前 topic 本身就是一个源文章
        return [topic]

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
9. 正文目标约400字，理想范围350-500字，硬范围200-800字；不要用空话凑字数
10. 多用短句和自然分段，每段约25-80字，适合手机阅读
11. 先用一句话点明“谁、发生了什么”，再写公开可确认的看点和讨论点
12. 语气像中文韩娱快讯：轻快、有一点粉丝视角，但克制、不尖叫、不造谣
13. 避免正式新闻稿和AI总结腔：不用“据悉、此外、值得注意的是、引发广泛关注、具有重要意义、展现国际影响力、从行业角度来看、文化输出”
14. 标题把艺人名放前面，信息点明确、简短自然，不要机器翻译感和夸张标题党
15. 涉及恋情、争议、法律回应时只复述来源已公开事实，不扩写私人细节，不把猜测写成事实
15.1 禁止“被指与女友调情、真实颜值、隐藏颜值、借题发挥、具体细节我们不再展开、恋情实锤、暧昧、翻车、疑似塌房、网友怒批”等刺激或暗示性表达
15.2 改用“相关片段引发讨论、网友看法不一、粉丝呼吁别过度解读、评论区观点不一”等中性表述
16. 结尾轻轻收束，可自然提到后续动态或留下一个克制的问题，不上价值
17. 韩国人名统一用中文译名（如"宋智孝""苏志燮"），团体名可用国际通用名

输出格式：严格JSON，包含 title, summary, content_text, content_html 字段。"""

    def _build_writing_prompt(
        self, topic: Dict, source_articles: List[Dict], tavily_images: List[Dict]
    ) -> str:
        """构建写作提示词"""
        # 构建源文章参考
        source_material = self._build_source_material(source_articles)

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

## 可用图片信息（仅用于判断图片数量，不要写具体画面）
{image_info}

## 写作要求
1. **语言**: 全文简体中文，韩语/英语源文必须翻译，韩国人名用中文译名
2. **字数**: 目标约400字，理想350-500字，允许200-800字；少于200字必须补写，超过800字必须压缩，不写空话
3. **文风**: 轻快自然的韩娱快讯。少正式新闻腔，先说谁发生了什么，再写公开看点和讨论
4. **段落**: 多用短句，每段25-80字，手机阅读时不要出现长句堆叠
5. **禁止口吻**: {', '.join(banned[:8])} 等饭圈表达
6. **安全表达**: 可用 {', '.join(safe[:5])}
7. **格式**: {"不输出Markdown标题符号" if rules.get("no_markdown_headings") else ""} {"不输出hashtag" if rules.get("no_hashtags") else ""}
8. **事实边界**: 粉丝/网友反应只能使用源材料明确提供的内容；争议与传闻不扩写、不定性；不用“女友、调情、真实颜值、隐藏颜值、借题发挥、恋情实锤、暧昧、翻车、疑似塌房、网友怒批”
9. **图片边界**: 未确认的服装、动作、表情、背景、构图一律不写，可用“从公开内容来看”“相关物料公开后”
10. **标题**: 艺人名放前面，信息点明确、自然简短，不要机器翻译腔
11. **禁用新闻稿腔**: 据悉、此外、值得注意的是、引发广泛关注、具有重要意义、展现国际影响力、从行业角度来看
12. **结尾**: 轻轻收束，不上价值，不使用强制点赞关注类互动

## 输出格式（严格JSON）
```json
{{
  "title": "文章标题（简体中文，不含饭圈词汇）",
  "summary": "摘要（简体中文，≤120字）",
  "content_text": "纯文本正文（简体中文，目标约400字，理想350-500字，硬范围200-800字）",
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
            content = article.get("content", "") or article.get("raw_content", "")
            content_preview = content[:600] if content else "（无内容）"

            parts.append(
                f"### 源文章 {i+1}: {title}\n"
                f"内容参考:\n{content_preview}\n"
            )

        return "\n---\n".join(parts)

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

    def _strict_check(self, parsed: Dict) -> Dict:
        """严格检查输出是否符合规则"""
        issues = []

        content = parsed.get("content_text", "")
        title = parsed.get("title", "")
        summary = parsed.get("summary", "")

        # 检查韩语字符 — 所有输出必须是中文
        korean_pattern = re.compile(r'[\uac00-\ud7af\u1100-\u11ff\u3130-\u318f]')
        if korean_pattern.search(title):
            issues.append("标题含韩语字符，必须翻译为中文")
        if korean_pattern.search(summary):
            issues.append("摘要含韩语字符，必须翻译为中文")
        if korean_pattern.search(content):
            # 找出韩语片段以便调试
            korean_fragments = korean_pattern.findall(content)
            issues.append(f"正文含韩语字符({len(korean_fragments)}处)，必须翻译为中文")

        # 检查字数
        char_count = len(content.replace(" ", "").replace("\n", ""))
        if char_count < 200:
            issues.append(f"字数不足({char_count} < 200)")
        elif char_count > 800:
            issues.append(f"字数超标({char_count} > 800)")

        # 检查禁止口吻
        banned = self.get_config("writer_agent.banned_phrases", [])
        for phrase in banned:
            if phrase in content or phrase in title:
                issues.append(f"含禁止口吻: '{phrase}'")

        # 检查 hashtag
        if re.search(r'#\w', content):
            issues.append("含hashtag")

        # 检查 emoji
        if re.search(r'[\U0001F000-\U0001FFFF\U00002600-\U000027FF]', content):
            issues.append("含emoji")

        # 检查 Markdown 标题符号
        if re.search(r'^#+\s', content, re.MULTILINE):
            issues.append("含Markdown标题符号")

        # 检查标题是否在正文重复
        if title and title in content:
            issues.append("标题在正文重复")

        # 检查具体图片描述（禁止编造）
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
                issues.append(f"疑似编造图片细节: 匹配'{pattern}'")

        return {
            "passed": len(issues) == 0,
            "issues": issues,
        }

    async def _retry_with_stricter_prompt(
        self, topic, source_articles, tavily_images, issues
    ) -> Optional[Dict]:
        """使用更严格的提示词重试"""
        logger.info(f"[写作Agent] 重试生成，问题: {issues}")

        stricter_prompt = f"""上次生成的内容有以下问题: {', '.join(issues)}

请重新生成，特别注意:
1. 所有内容必须是简体中文，韩语/英语必须完全翻译
2. 绝对不要出现上述问题
3. 目标约400字，优先控制在350-500字；少于200字必须补写，超过800字必须压缩
4. 不要写任何具体图片画面细节
5. 不要使用饭圈口吻
6. 韩国人名用中文译名
7. 争议内容使用中性表达，不写女友、调情、真实颜值、借题发挥、恋情实锤、暧昧、翻车或疑似塌房

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
