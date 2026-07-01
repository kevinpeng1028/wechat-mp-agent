"""① 选题 Agent - 韩国娱乐媒体搜索 + Tavily site:搜索 + 3篇预选 + 综合评分"""

import asyncio
import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx

from src.base_agent import BaseAgent, AgentStatus, AgentResult
from src.config import Config
from src.logger import logger
from src.topic.scoring import ScoringSystem

# 不能作为封面图或正文图的关键词
BLOCKED_KEYWORDS = [
    "audition", "apply", "recruit", "trainee", "casting",
    "banner", "logo", "ad_", "_ad", "advert", "sponsor",
    "button", "icon", "favicon", "header_img", "footer_img",
    "navigation", "menu", "sidebar",
]

# 只允许进入公众号候选池的 K-pop 音乐类关键词
KPOP_MUSIC_KEYWORDS = [
    "k-pop", "kpop", "idol", "group", "boy group", "girl group",
    "comeback", "album", "single", "ep", "mini album", "full album",
    "music video", "mv", "teaser", "concept photo", "tracklist",
    "stage", "performance", "concert", "tour", "fanmeeting", "fan meeting",
    "showcase", "festival", "music bank", "inkigayo", "m countdown",
    "billboard", "melon", "spotify", "chart", "streaming",
    "debut", "release", "dance practice", "vocal", "rapper", "member",
    "男团", "女团", "爱豆", "偶像", "回归", "专辑", "单曲", "新歌",
    "舞台", "打歌", "演唱会", "巡演", "见面会", "预告", "概念照",
    "音源", "榜单", "出道", "成员", "组合",
]

# 默认排除影视、恋情、分手、演员八卦类内容
NON_MUSIC_BLOCK_KEYWORDS = [
    "actor", "actress", "drama", "movie", "film", "netflix", "series",
    "dating", "date", "breakup", "break up", "boyfriend", "girlfriend",
    "lover", "relationship", "couple", "marriage", "divorce", "rumor",
    "scandal", "kiss", "romance", "wedding", "pregnant",
    "演员", "女演员", "男演员", "韩剧", "电视剧", "电影", "网飞",
    "恋情", "约会", "分手", "男友", "女友", "恋人", "情侣",
    "结婚", "离婚", "传闻", "绯闻", "恋爱", "破局",
]

# 明显不适合做爱豆音乐公众号封面/选题的聚合页标题
BAD_TOPIC_PATTERNS = [
    "artist tag", "all kpop all the time", "tag -",
]




# 必须命中这些“强音乐关键词”，才算真正适合公众号
STRONG_KPOP_MUSIC_KEYWORDS = [
    "comeback", "album", "single", "ep", "mini album", "full album",
    "music video", "mv", "teaser", "concept photo", "tracklist",
    "stage", "performance", "concert", "tour", "fanmeeting", "fan meeting",
    "showcase", "festival", "music bank", "inkigayo", "m countdown",
    "billboard", "world albums", "melon", "spotify", "chart", "streaming",
    "debut", "release", "dance practice", "vocal", "rapper",
    "回归", "专辑", "单曲", "新歌", "MV", "音乐视频", "舞台", "打歌",
    "演唱会", "巡演", "见面会", "预告", "概念照", "音源", "榜单", "出道",
]

# 即使命中艺人也不采用的低质/生活类主题
IDOL_TOPIC_HARD_BLOCK_KEYWORDS = [
    "subway", "cafeteria", "cheap meal", "meal", "restaurant", "food",
    "brand reputation", "reputation rankings", "star brand reputation",
    "地铁", "食堂", "餐厅", "餐饮", "廉价餐", "品牌声誉", "声誉榜",
]

IDOL_FASHION_EVENT_KEYWORDS = [
    "fashion week", "celine", "dior", "chanel", "brand event",
    "airport", "after-party", "after party", "red carpet", "fashion show",
]

IDOL_BUZZ_GOSSIP_KEYWORDS = [
    "sparks attention", "fans react", "netizens react", "goes viral", "viral",
    "dating", "relationship", "rumor", "scandal", "controversy", "debate",
]

IDOL_LEGAL_RESPONSE_KEYWORDS = [
    "legal action", "lawsuit", "protect", "protection", "company response",
    "agency response", "hybe", "bighit", "sm entertainment",
    "jyp entertainment", "yg entertainment",
]

IDOL_BUSINESS_CAREER_KEYWORDS = [
    "investor", "investment", "startup", "business", "venture",
    "ambassador", "collaboration", "campaign",
]

# 常见媒体写法与 TOP_STARS 中标准团名的别名
IDOL_TARGET_ALIASES = [
    "i-dle", "gidle", "(g)i-dle", "lesserafim",
]

AMBIGUOUS_STANDALONE_TARGETS = {
    "Jin", "V", "RM", "Han", "Jay", "Jake", "Mark", "Ten",
    "Rei", "Bae", "Lia", "Isa",
}

GENERIC_SCREEN_MEDIA_KEYWORDS = [
    "actor", "actress", "drama", "movie", "film", "documentary",
    "to star in", "cast", "casting",
]

BAD_TOPIC_PATTERNS = [
    "artist tag", "all kpop all the time", "tag -",
]

def _is_blocked_image_url(url: str) -> bool:
    """检查图片 URL 是否包含被禁关键词"""
    url_lower = url.lower()
    for kw in BLOCKED_KEYWORDS:
        if kw in url_lower:
            return True
    return False


class TopicAgent(BaseAgent):
    """
    选题 Agent 职责：
    1. 使用 Tavily 搜索韩国娱乐媒体（site: 限定）
    2. 每天搜索3篇预选文章
    3. 24小时内素材过滤（严格24h，不回退）
    4. 使用综合评分系统打分（15个字段）
    5. 选最高分的2篇（头条 + 次条）
    6. 图文一致性检查（不低于阈值）
    7. 每篇文章最多3张图片（原文配图）
    """

    name = "topic_agent"
    display_name = "① 选题 Agent (韩国娱乐搜索+评分)"

    def __init__(self, config: Optional[Dict] = None):
        super().__init__(config)
        self.llm_client = self._init_llm()
        self.http_client = None
        self.scoring = ScoringSystem(config or {})
        search_cfg = self.get_config("topic_agent.search", {})
        self.candidate_count = search_cfg.get("candidate_count", 5)
        self.selected_count = search_cfg.get("selected_count", 2)
        self.freshness_hours = search_cfg.get("freshness_hours", 24)
        self.min_images = search_cfg.get("min_images_per_article", 1)
        self.max_core_tavily_queries = search_cfg.get(
            "max_core_tavily_queries", 6
        )
        self.max_extra_tavily_queries = search_cfg.get("max_extra_tavily_queries", 4)
        self.max_image_search_queries = search_cfg.get("max_image_search_queries", 2)
        self.max_total_tavily_credits = search_cfg.get(
            "max_total_tavily_credits_per_run", 12
        )
        self.hard_stop_tavily_credits = search_cfg.get(
            "hard_stop_tavily_credits_per_run", 20
        )
        self.absolute_max_tavily_credits = search_cfg.get(
            "absolute_max_tavily_credits_per_run", 50
        )
        configured_depth = search_cfg.get("search_depth", "basic")
        allow_advanced = search_cfg.get("allow_advanced_search", False)
        self.search_depth = (
            configured_depth
            if configured_depth != "advanced" or allow_advanced
            else "basic"
        )
        self.auto_parameters = bool(search_cfg.get("auto_parameters", False))
        self.max_tavily_queries = (
            self.max_core_tavily_queries + self.max_extra_tavily_queries
        )
        self.use_search_cache = search_cfg.get("use_search_cache", True)
        self.min_candidates_before_extra = search_cfg.get(
            "min_candidates_before_extra_search", 3
        )
        self._actual_tavily_calls = 0
        self._cache_hits = 0
        self._core_tavily_calls = 0
        self._extra_tavily_calls = 0
        self._tavily_budget = {
            "calls": 0,
            "credits": 0,
            "cache_hits": 0,
            "image_calls": 0,
            "max_image_calls": self.max_image_search_queries,
            "max_total_credits": self.max_total_tavily_credits,
            "hard_stop_credits": self.hard_stop_tavily_credits,
            "absolute_max_credits": self.absolute_max_tavily_credits,
            "hard_stop_triggered": False,
        }
        # 进程内缓存 + 同日磁盘缓存
        self._search_cache: Dict[str, Tuple[float, List[Dict]]] = {}
        project_root = Path((config or {}).get("project_root", "."))
        self._daily_cache_path = (
            project_root / "data" / "topics"
            / f"search_cache_{datetime.now().strftime('%Y-%m-%d')}.json"
        )
        self._load_daily_search_cache()

    def _init_llm(self):
        from openai import AsyncOpenAI
        return AsyncOpenAI(
            api_key=self.get_config("llm.api_key"),
            base_url=self.get_config("llm.base_url"),
        )

    async def _get_http(self) -> httpx.AsyncClient:
        if self.http_client is None:
            self.http_client = httpx.AsyncClient(timeout=30.0)
        return self.http_client

    async def execute(self, context: Dict) -> AgentResult:
        """
        执行选题流程：
        1. 用 Tavily 搜索韩国娱乐媒体（多源组合搜索）
        2. 24h时效性过滤
        3. 综合评分（15个字段）
        4. 选最高2篇 -> 头条 + 次条
        """
        logger.info("[选题Agent] 开始执行韩国娱乐圈选题流程...")

        # Step 1: Tavily 多源搜索
        candidates = await self._search_korean_entertainment()

        if not candidates:
            return AgentResult(
                status=AgentStatus.FAILED,
                agent_name=self.name,
                error="未从韩国娱乐媒体搜索到任何候选文章",
            )

        logger.info(f"[选题Agent] 搜索到 {len(candidates)} 篇候选文章")

        # Step 1.5: 过滤掉没有图片的文章（必须自带图片，不自行抓取）
        before_filter = len(candidates)
        candidates = [c for c in candidates if c.get("_tavily_images")]
        logger.info(
            f"[选题Agent] 图片过滤: {before_filter} → {len(candidates)} 篇 "
            f"(排除 {before_filter - len(candidates)} 篇无图文章)"
        )

        if not candidates:
            return AgentResult(
                status=AgentStatus.FAILED,
                agent_name=self.name,
                error="所有候选文章均无配图，按规则跳过（不自行抓取图片）",
            )

        # Step 2: 24h 时效性过滤（严格24小时，不回退到48h）
        fresh_candidates = self._filter_freshness(candidates)
        logger.info(f"[选题Agent] 24h内素材: {len(fresh_candidates)} 篇")

        if not fresh_candidates:
            return AgentResult(
                status=AgentStatus.FAILED,
                agent_name=self.name,
                error="未找到24小时内的候选文章（严格过滤，不回退到48h）",
            )

        # Step 2.5: Idol-centric 硬过滤。音乐动态优先，但成员个人、
        # 时尚活动、粉丝热议、争议传闻、法律维权和个人事业同样允许。
        before_idol_filter = len(fresh_candidates)
        fresh_candidates = self._filter_idol_centric_topics(fresh_candidates)
        logger.info(
            f"[选题Agent] Idol-centric过滤: {before_idol_filter} → "
            f"{len(fresh_candidates)} 篇 "
            f"(排除 {before_idol_filter - len(fresh_candidates)} 篇无关/低质/聚合页)"
        )

        if not fresh_candidates:
            return AgentResult(
                status=AgentStatus.FAILED,
                agent_name=self.name,
                error="未找到明确涉及目标韩国男团、女团或成员的合格候选文章",
            )

        # 只截断已经通过 Idol-centric 硬过滤的候选（评分成本考虑）
        if len(fresh_candidates) > self.candidate_count * 3:
            fresh_candidates = sorted(
                fresh_candidates,
                key=lambda c: (self._hours_ago(c), -(c.get("score", 0) or 0))
            )[:self.candidate_count * 3]

        # Step 2.6: 目标韩国男团、女团及成员过滤
        fresh_candidates = self._filter_top_stars(fresh_candidates)
        logger.info(f"[选题Agent] 顶流明星过滤后: {len(fresh_candidates)} 篇")

        if not fresh_candidates:
            return AgentResult(
                status=AgentStatus.FAILED,
                agent_name=self.name,
                error="未找到涉及目标韩国男团、女团或成员的候选文章",
            )

        # Step 3: 综合评分（15个字段）
        scored = []
        for article in fresh_candidates:
            tavily_imgs = article.get("_tavily_images", [])

            # 并行评分
            score_result = await self.scoring.score_article(
                article, tavily_imgs, self.llm_client
            )

            scored.append({
                **article,
                "scores": score_result,
            })

        # Step 4: 排序选优
        scored_sorted = sorted(
            scored,
            key=lambda x: x.get("scores", {}).get("total_score", 0),
            reverse=True
        )

        # Step 5: 过滤不满足 ready 条件的文章
        ready_articles = []
        for article in scored_sorted:
            is_ready, reason = self.scoring.is_ready_for_draft(article.get("scores", {}))
            if is_ready:
                ready_articles.append(article)
            else:
                logger.info(
                    f"[选题Agent] ❌ 不满足ready: {article.get('title','?')[:40]} | {reason}"
                )

        if not ready_articles:
            return AgentResult(
                status=AgentStatus.FAILED,
                agent_name=self.name,
                error="所有候选文章均不满足 ready 条件（图文一致性/风险/重复）",
            )

        # Step 6: 最多选配置数量；只有 1 篇合格时就只输出 1 篇
        selected = ready_articles[:self.selected_count]

        # 标记头条/次条
        if len(selected) >= 1:
            selected[0]["position"] = "headline"  # 头条
        if len(selected) >= 2:
            selected[1]["position"] = "sub_headline"  # 次条

        # 保存选题历史
        await self._save_topic_history(selected, context)

        # 打印选题摘要
        self._print_topic_summary(selected)

        return AgentResult(
            status=AgentStatus.SUCCESS,
            agent_name=self.name,
            output={
                "total_candidates": len(candidates),
                "fresh_candidates": len(fresh_candidates),
                "scored_count": len(scored),
                "ready_count": len(ready_articles),
                "selected_topics": selected,
                "ready_articles": ready_articles,
                "tavily_budget": self._tavily_budget,
                "all_scored": scored_sorted[:10],  # 保留前10名供参考
                "trigger_type": context.get("trigger_type", "manual"),
            },
        )

    # ==================== Tavily 搜索 ====================

    async def _search_korean_entertainment(self) -> List[Dict]:
        """
        使用 Tavily 搜索韩国娱乐媒体

        策略：多轮搜索
        1. 通用关键词搜索（含 site: 限定）
        2. 搜索结果去重合并
        3. 返回候选文章列表
        """
        api_key = self.get_config("tavily.api_key", "")
        if not api_key or api_key.startswith("${"):
            logger.error("[选题Agent] Tavily API Key 未配置！")
            return []

        all_results = []
        seen_urls = set()
        tiers = self._build_tiered_search_queries()

        async def run_queries(queries: List[Dict], purpose: str, limit: int):
            used = (
                self._core_tavily_calls if purpose == "core"
                else self._extra_tavily_calls
            )
            remaining = max(0, limit - used)
            for i in range(0, min(len(queries), remaining), 3):
                batch = queries[i:i + min(3, remaining - i)]
                for query in batch:
                    query["budget_purpose"] = purpose
                results = await asyncio.gather(
                    *(self._tavily_search(query) for query in batch)
                )
                for query_result in results:
                    for article in query_result or []:
                        url = article.get("url", "")
                        if url and url not in seen_urls:
                            seen_urls.add(url)
                            all_results.append(article)

        await run_queries(
            tiers["primary"], "core", self.max_core_tavily_queries
        )
        eligible = len(self._filter_idol_centric_topics(all_results))
        if eligible < self.min_candidates_before_extra:
            await run_queries(
                tiers["secondary"], "extra", self.max_extra_tavily_queries
            )
            eligible = len(self._filter_idol_centric_topics(all_results))
        if eligible < 2:
            remaining_extra = max(
                0, self.max_extra_tavily_queries - self._extra_tavily_calls
            )
            await run_queries(tiers["supplemental"], "extra", remaining_extra)

        logger.info(
            f"[选题Agent] Tavily搜索完成: {len(all_results)} 篇独特文章 | "
            f"实际调用 {self._actual_tavily_calls} | "
            f"预计credits {self._tavily_budget['credits']}/"
            f"{self.max_total_tavily_credits} | "
            f"缓存命中 {self._cache_hits} | "
            f"剩余credits {max(0, self.max_total_tavily_credits-self._tavily_budget['credits'])} | "
            f"hard stop={self._tavily_budget['hard_stop_triggered']}"
        )
        return all_results

    def _build_tiered_search_queries(self) -> Dict[str, List[Dict]]:
        primary = [
            ("soompi.com", "Soompi", "Stray Kids SEVENTEEN TXT ENHYPEN concert album"),
            ("allkpop.com", "AllKpop", "IVE LE SSERAFIM BABYMONSTER i-dle MV teaser comeback"),
            ("koreaboo.com", "Koreaboo", "BTS BLACKPINK NewJeans aespa fans react goes viral"),
            ("sbsstar.net", "SBS Star", "TWICE ITZY NMIXX Red Velvet latest controversy"),
            ("kpopstarz.com", "KpopStarz", "BTS V Suga Jennie Lisa Karina Wonyoung fashion week airport"),
            ("nme.com", "NME K-pop", "k-pop BTS BLACKPINK aespa IVE latest"),
            ("billboard.com", "Billboard K-pop", "k-pop BTS BLACKPINK Stray Kids NewJeans chart"),
        ]
        secondary = [
            ("entertain.naver.com", "Naver Entertainment", "BTS BLACKPINK aespa IVE"),
            ("osen.co.kr", "OSEN", "BTS aespa IVE BLACKPINK"),
            ("starnewskorea.com", "StarNews", "BTS BLACKPINK NewJeans aespa"),
            ("newsen.com", "NewsEn", "BTS BLACKPINK aespa IVE"),
            ("xportsnews.com", "XportsNews", "BTS BLACKPINK aespa IVE"),
            ("mydaily.co.kr", "MyDaily", "BTS BLACKPINK aespa IVE"),
            ("dispatch.co.kr", "Dispatch", "BTS BLACKPINK aespa IVE"),
            ("tenasia.com", "TenAsia", "BTS BLACKPINK aespa IVE"),
            ("sports.chosun.com", "Sports Chosun", "BTS BLACKPINK aespa IVE"),
            ("tvreport.co.kr", "TVReport", "BTS BLACKPINK aespa IVE"),
        ]
        supplemental = [
            ("", "supplemental", "BTS BLACKPINK aespa IVE fans react goes viral airport fashion week brand event"),
            ("", "supplemental", "BTS BLACKPINK Stray Kids dating rumor controversy"),
            ("", "supplemental", "HYBE responds SM responds JYP responds YG responds protects artists legal action"),
        ]
        primary_hours = self.get_config(
            "topic_agent.search.freshness_hours_primary", 24
        )
        secondary_hours = self.get_config(
            "topic_agent.search.freshness_hours_secondary", 72
        )
        def pack(rows, freshness_hours):
            return [{
                "query": (f"site:{site} " if site else "") + suffix,
                "site": site,
                "source_name": name,
                "freshness_hours": freshness_hours,
            } for site, name, suffix in rows]
        return {
            "primary": pack(primary, primary_hours),
            "secondary": pack(secondary, secondary_hours),
            "supplemental": pack(supplemental, secondary_hours),
        }

    def _load_daily_search_cache(self):
        if not self.use_search_cache or not self._daily_cache_path.exists():
            return
        try:
            data = json.loads(self._daily_cache_path.read_text(encoding="utf-8"))
            for key, entry in data.items():
                results = entry.get("results", [])
                for article in results:
                    article["published_date"] = self._parse_date(
                        article.get("published_date_str", "")
                    )
                self._search_cache[key] = (entry.get("created_ts", 0), results)
        except Exception as exc:
            logger.warning(f"[选题Agent] 搜索缓存读取失败，忽略: {exc}")

    def _save_daily_search_cache(self):
        if not self.use_search_cache:
            return
        try:
            self._daily_cache_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {}
            for key, (created_ts, results) in self._search_cache.items():
                serializable = []
                for article in results:
                    item = dict(article)
                    item.pop("published_date", None)
                    serializable.append(item)
                payload[key] = {
                    "created_ts": created_ts,
                    "created_at": datetime.fromtimestamp(created_ts).isoformat(),
                    "results": serializable,
                }
            self._daily_cache_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.warning(f"[选题Agent] 搜索缓存写入失败，忽略: {exc}")

    def _build_search_queries(
        self, search_sources: List[Dict], general_keywords: List[str]
    ) -> List[Dict]:
        """
        构建搜索查询列表

        每条查询包含:
        - query: 搜索字符串（含 site: 限定）
        - query_suffix: 附加关键词
        """
        queries = []

        # 使用 site: 限定搜索源
        for source in search_sources:
            site = source.get("site", "")
            suffix = source.get("query_suffix", "")
            query_str = f'site:{site} {suffix}'
            queries.append({
                "query": query_str,
                "site": site,
                "source_name": source.get("name", site),
            })

        # 补充通用关键词搜索（不限定站点）
        for keyword in general_keywords[:5]:  # 限制数量，避免API消耗过多
            queries.append({
                "query": keyword,
                "site": "",
                "source_name": "general",
            })

        return queries

    async def _tavily_search(self, query_info: Dict) -> List[Dict]:
        """执行单次 Tavily 搜索（同一天同 query 优先命中缓存）。"""
        import time

        query = query_info["query"]
        source_name = query_info.get("source_name", "unknown")
        freshness_hours = query_info.get("freshness_hours", self.freshness_hours)
        time_range = "day" if freshness_hours <= 24 else "week"
        cache_key = f"{query}|{time_range}"

        if self.use_search_cache and cache_key in self._search_cache:
            self._cache_hits += 1
            self._tavily_budget["cache_hits"] += 1
            cached_result = self._search_cache[cache_key][1]
            logger.info(
                f"[选题Agent] 🗄️ 命中当日缓存 | {source_name} | "
                f"query='{query[:50]}' → {len(cached_result)} 篇"
            )
            return cached_result
        credit_cost = 2 if self.search_depth == "advanced" else 1
        if not self._reserve_tavily_credits(credit_cost, query):
            return []
        self._actual_tavily_calls += 1
        self._tavily_budget["calls"] += 1
        purpose = query_info.get("budget_purpose", "core")
        if purpose == "core":
            self._core_tavily_calls += 1
        else:
            self._extra_tavily_calls += 1

        api_key = self.get_config("tavily.api_key", "")
        base_url = self.get_config("tavily.base_url", "https://api.tavily.com")
        max_results = min(self.get_config("tavily.max_results", 10), 3)  # 优化：每次最多3条，降低费用

        payload = {
            "query": query,
            "search_depth": self.search_depth,
            "auto_parameters": False,
            "include_answer": False,
            "max_results": max_results,
            "include_images": True,
            "include_image_descriptions": True,
            "include_raw_content": False,
            "time_range": time_range,
            "topic": "news",
        }

        http = await self._get_http()

        try:
            response = await http.post(
                f"{base_url}/search",
                json=payload,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
            )
            response.raise_for_status()

            data = response.json()

            articles = []

            for r in data.get("results", []):
                # 尝试提取发布时间
                published_str = r.get("published_date", "")
                published_dt = self._parse_date(published_str)

                article = {
                    "title": r.get("title", ""),
                    "url": r.get("url", ""),
                    "content": r.get("content", ""),
                    "score": r.get("score", 0),
                    "raw_content": r.get("raw_content", ""),
                    "published_date": published_dt,
                    "published_date_str": published_str,
                    "source_name": source_name,
                    "source_site": query_info.get("site", ""),
                    "_freshness_hours": freshness_hours,
                    "_tavily_images": [],
                }

                # 只使用该文章自己的图片（r.get("images")），不使用全局 top_images
                # 限制每篇文章最多3张图片，减少 Tavily 消耗
                # 同时过滤 LOGO/广告/选秀等无效图片
                article_images = r.get("images", [])[:3]
                img_map = {}
                for img in article_images:
                    if isinstance(img, dict):
                        url = img.get("url", "")
                        if url and url not in img_map and not _is_blocked_image_url(url):
                            img_map[url] = img
                    elif isinstance(img, str) and img not in img_map and not _is_blocked_image_url(img):
                        img_map[img] = {"url": img}

                article["_tavily_images"] = list(img_map.values())
                articles.append(article)

            # 写入缓存
            import time
            self._search_cache[cache_key] = (time.time(), articles)
            self._save_daily_search_cache()

            logger.info(
                f"[选题Agent] Tavily | {source_name} | query='{query[:60]}' → {len(articles)} 篇"
            )

            return articles

        except httpx.HTTPStatusError as e:
            logger.error(
                f"[选题Agent] Tavily API 错误 [{source_name}]: "
                f"{e.response.status_code} - {e.response.text[:200]}"
            )
            return []
        except Exception as e:
            logger.error(f"[选题Agent] Tavily 搜索异常 [{source_name}]: {e}")
            return []

    def _reserve_tavily_credits(self, cost: int, query: str = "") -> bool:
        current = self._tavily_budget["credits"]
        projected = current + cost
        if projected > self.absolute_max_tavily_credits:
            self._tavily_budget["hard_stop_triggered"] = True
            logger.error(
                f"[选题Agent] Tavily absolute max 触发: {projected}>"
                f"{self.absolute_max_tavily_credits}，强制终止调用"
            )
            return False
        if projected > self.hard_stop_tavily_credits:
            self._tavily_budget["hard_stop_triggered"] = True
            logger.error(
                f"[选题Agent] Tavily hard stop 触发: {projected}>"
                f"{self.hard_stop_tavily_credits}，停止追加搜索"
            )
            return False
        if projected > self.max_total_tavily_credits:
            logger.warning(
                f"[选题Agent] Tavily 默认credits预算耗尽，跳过: {query[:60]}"
            )
            return False
        self._tavily_budget["credits"] = projected
        return True

    # ==================== 顶流明星过滤 ====================

    # 跳过聚合页/分类页 URL 模式（这类页面 raw_content 包含大量外链，导致误匹配）
    _SKIP_URL_PATTERNS = [
        "/artisttag/", "/category/", "/cat/", "/tag/", "/tags/",
        "/profile/", "/artist/", "/movies",
        "/playlist/",
        "/archives/", "/archive/",
        "/section/", "/topic/", "/channel/",
        "/page/", "/p=",
    ]

    def _is_aggregate_page(self, url: str) -> bool:
        """检查 URL 是否为聚合页/分类页"""
        url_lower = url.lower()
        for pattern in self._SKIP_URL_PATTERNS:
            if pattern in url_lower:
                return True
        return False

    @staticmethod
    def _contains_topic_keyword(text: str, keyword: str) -> bool:
        """英文关键词按单词边界匹配，避免短词在普通 URL 中误命中。"""
        keyword = keyword.lower()
        if re.fullmatch(r"[a-z0-9][a-z0-9 -]*", keyword):
            escaped = re.escape(keyword).replace(r"\ ", r"\s+")
            return re.search(
                rf"(?<![a-z0-9]){escaped}s?(?![a-z0-9])",
                text,
            ) is not None
        return keyword in text

    def _filter_idol_centric_topics(self, candidates: List[Dict]) -> List[Dict]:
        """
        Idol-centric 硬过滤：标题、URL 或摘要必须明确命中目标团体/成员。
        音乐新闻优先，但个人动态、时尚活动、热议八卦、法律维权及
        商业事业均可进入评分。正文和 raw_content 不参与艺人准入判断。
        """
        filtered = []

        for article in candidates:
            title = article.get("title", "") or ""
            url = article.get("url", "") or ""
            search_summary = (
                article.get("summary", "")
                or article.get("description", "")
                or ""
            )

            visible_text = f"{title} {url} {search_summary}".lower()

            if self._is_aggregate_page(url) or any(
                p in visible_text for p in BAD_TOPIC_PATTERNS
            ):
                logger.info(
                    f"[选题Agent] 🚫 过滤聚合页/标签页: {title[:80]} | {url[:80]}"
                )
                continue

            target_hits = [
                star for star in ScoringSystem.TOP_STARS
                if ScoringSystem._match_star(star, visible_text)
            ]
            target_hits.extend(
                alias for alias in IDOL_TARGET_ALIASES
                if self._contains_topic_keyword(visible_text, alias)
            )
            if not target_hits:
                logger.info(
                    f"[选题Agent] 🚫 过滤：标题/URL/摘要未命中目标团体或成员: "
                    f"{title[:80]}"
                )
                continue

            media_keyword = next((
                keyword for keyword in GENERIC_SCREEN_MEDIA_KEYWORDS
                if self._contains_topic_keyword(visible_text, keyword)
            ), None)
            unambiguous_hits = [
                hit for hit in target_hits
                if hit not in AMBIGUOUS_STANDALONE_TARGETS
            ]
            if media_keyword and not unambiguous_hits:
                logger.info(
                    f"[选题Agent] 🚫 过滤普通影视内容：仅命中歧义短名 "
                    f"{target_hits[:3]}，影视词='{media_keyword}': {title[:80]}"
                )
                continue

            blocked_keyword = next((
                keyword for keyword in IDOL_TOPIC_HARD_BLOCK_KEYWORDS
                if self._contains_topic_keyword(visible_text, keyword)
            ), None)
            if blocked_keyword:
                logger.info(
                    f"[选题Agent] 🚫 过滤低质/生活主题 "
                    f"(艺人={target_hits[:3]}, 命中='{blocked_keyword}'): {title[:80]}"
                )
                continue

            categories = [
                ("音乐动态", STRONG_KPOP_MUSIC_KEYWORDS),
                ("时尚活动", IDOL_FASHION_EVENT_KEYWORDS),
                ("八卦热议", IDOL_BUZZ_GOSSIP_KEYWORDS),
                ("法律维权", IDOL_LEGAL_RESPONSE_KEYWORDS),
                ("商业/个人事业", IDOL_BUSINESS_CAREER_KEYWORDS),
            ]
            category = "爱豆个人动态"
            reason = "明确命中目标艺人"
            for category_name, keywords in categories:
                hit = next((
                    keyword for keyword in keywords
                    if self._contains_topic_keyword(visible_text, keyword)
                ), None)
                if hit:
                    category, reason = category_name, f"命中 '{hit}'"
                    break

            logger.info(
                f"[选题Agent] ✅ {category}通过 "
                f"(艺人={target_hits[:3]}, {reason}): {title[:80]}"
            )
            filtered.append(article)

        return filtered

    def _filter_kpop_music_topics(self, candidates: List[Dict]) -> List[Dict]:
        """向后兼容旧调用；实际执行 Idol-centric 过滤。"""
        return self._filter_idol_centric_topics(candidates)

    def _filter_top_stars(self, candidates: List[Dict]) -> List[Dict]:
        """过滤出真正关于顶流明星的文章

        匹配策略（避免侧边栏/推荐链接导致的误匹配）：
        标题、URL 或摘要必须命中目标艺人。正文/侧栏不用于准入，
        避免偶然提及造成误判。
        """
        from src.topic.scoring import ScoringSystem

        filtered = []
        for c in candidates:
            url = c.get("url", "")
            title = (c.get("title") or "")
            summary = (c.get("summary") or c.get("description") or "")
            visible_text = f"{title} {url} {summary}"

            # 仅跳过明确的聚合页/分类页；/news/<slug> 是正常文章链接
            if self._is_aggregate_page(url):
                logger.info(
                    f"[选题Agent] 🚫 顶流过滤：明确聚合页 URL: "
                    f"'{title[:30]}' | {url[:60]}"
                )
                continue

            visible_star_hits = []
            for star in ScoringSystem.TOP_STARS:
                if ScoringSystem._match_star(star, visible_text):
                    visible_star_hits.append(star)
            visible_star_hits.extend(
                alias for alias in IDOL_TARGET_ALIASES
                if self._contains_topic_keyword(visible_text.lower(), alias)
            )

            if visible_star_hits:
                filtered.append(c)
                logger.info(
                    f"[选题Agent] ✅ 目标艺人过滤通过 "
                    f"{visible_star_hits[:3]}: '{title[:60]}'"
                )
                continue

            logger.info(
                f"[选题Agent] 🚫 目标艺人过滤：标题/URL/摘要未命中: "
                f"'{title[:60]}'"
            )

        return filtered

    # ==================== 时效性过滤 ====================

    def _filter_freshness(self, candidates: List[Dict]) -> List[Dict]:
        """严格24小时内素材过滤

        规则：
        1. 有发布时间的文章，必须在24小时内
        2. 无发布时间的文章，检查标题/内容中是否含旧年份（2023/2024等），有则排除
        3. 不再保留无日期文章（避免旧文章混入）
        """
        now = datetime.now()
        current_year = now.year

        fresh = []
        rejected_old = 0
        rejected_nodate = 0

        for c in candidates:
            cutoff = now - timedelta(
                hours=c.get("_freshness_hours", self.freshness_hours)
            )
            pub = c.get("published_date")
            if pub and isinstance(pub, datetime):
                if pub >= cutoff:
                    fresh.append(c)
                else:
                    rejected_old += 1
                    logger.debug(
                        f"[选题Agent] 排除超时文章: '{(c.get('title') or '')[:40]}' "
                        f"发布于 {pub.strftime('%Y-%m-%d %H:%M')}"
                    )
            else:
                # 无发布时间，检查内容中是否有旧年份标记
                title = (c.get("title") or "").lower()
                content = (c.get("content") or "").lower()
                raw = (c.get("raw_content") or "").lower()
                full_text = title + " " + content + " " + raw

                # 排除含明确旧年份的文章（如 "2024年", "2023年", "in 2024" 等）
                has_old_year = False
                for year in range(2020, current_year):
                    year_str = str(year)
                    # 检查是否包含明确的年份引用（不是URL中的数字）
                    if year_str + "年" in full_text or year_str + "-" in title:
                        has_old_year = True
                        break
                    # 检查 "in 2024" 或 "2024." 等模式
                    if re.search(r'\b' + year_str + r'\b', title):
                        has_old_year = True
                        break

                if has_old_year:
                    rejected_old += 1
                    logger.debug(
                        f"[选题Agent] 排除旧年份文章: '{(c.get('title') or '')[:40]}'"
                    )
                else:
                    # 无日期且无旧年份标记，保守保留（Tavily time_range="day" 已限制搜索范围）
                    fresh.append(c)
                    rejected_nodate += 0  # 不计数，保留

        if rejected_old > 0:
            logger.info(
                f"[选题Agent] 时效性过滤: 排除 {rejected_old} 篇超时/旧年份文章"
            )

        return fresh

    def _hours_ago(self, article: Dict) -> float:
        """文章发布至今的小时数"""
        pub = article.get("published_date")
        if not pub or not isinstance(pub, datetime):
            return 999.0
        return (datetime.now() - pub).total_seconds() / 3600

    def _parse_date(self, date_str: str) -> Optional[datetime]:
        """解析日期字符串"""
        if not date_str:
            return None
        try:
            # 尝试常见格式
            for fmt in [
                "%Y-%m-%dT%H:%M:%S%z",
                "%Y-%m-%dT%H:%M:%SZ",
                "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d",
            ]:
                try:
                    dt = datetime.strptime(date_str, fmt)
                    if dt.tzinfo:
                        dt = dt.replace(tzinfo=None)
                    return dt
                except ValueError:
                    continue
        except Exception:
            pass
        return None

    # ==================== 辅助方法 ====================

    async def _save_topic_history(self, topics: List[Dict], context: Dict):
        """保存选题历史"""
        data_dir = Path(self.get_config("project_root", ".")) / "data" / "topics"
        data_dir.mkdir(parents=True, exist_ok=True)

        today = datetime.now().strftime("%Y-%m-%d")
        history_file = data_dir / f"{today}.json"

        record = {
            "timestamp": datetime.now().isoformat(),
            "trigger_type": context.get("trigger_type"),
            "topics": [
                {
                    "title": t.get("title", ""),
                    "url": t.get("url", ""),
                    "position": t.get("position", ""),
                    "source_name": t.get("source_name", ""),
                    "scores": t.get("scores", {}),
                    "total_score": t.get("scores", {}).get("total_score", 0),
                }
                for t in topics
            ],
        }

        existing = []
        if history_file.exists():
            with open(history_file, "r", encoding="utf-8") as f:
                existing = json.load(f)
        existing.append(record)

        with open(history_file, "w", encoding="utf-8") as f:
            json.dump(existing, f, ensure_ascii=False, indent=2)

        logger.info(f"[选题Agent] 选题历史已保存: {history_file}")

    def _print_topic_summary(self, selected: List[Dict]):
        """打印选题摘要"""
        from rich.console import Console
        from rich.table import Table

        console = Console()
        table = Table(title="📋 今日选题结果", show_header=True)
        table.add_column("位置", style="bold")
        table.add_column("标题", style="cyan")
        table.add_column("来源", style="dim")
        table.add_column("总分", justify="right")
        table.add_column("图文", justify="right")
        table.add_column("风险", justify="right")

        for article in selected:
            scores = article.get("scores", {})
            pos = article.get("position", "?")
            pos_label = "🔥 头条" if pos == "headline" else "📰 次条"

            table.add_row(
                pos_label,
                article.get("title", "?")[:50],
                article.get("source_name", "?"),
                f"{scores.get('total_score', 0):.1f}",
                f"{scores.get('image_relevance_score', 0):.1f}",
                f"{scores.get('risk_score', 0):.1f}",
            )

        console.print(table)

    async def cleanup(self):
        if self.http_client:
            await self.http_client.aclose()
