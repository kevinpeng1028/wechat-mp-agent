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
    "artist tag", "all kpop all the time", "tag -", "/tag/",
    "category", "archive", "profile", "author",
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

# 这些内容即使命中爱豆名字，也默认不做
STRICT_NON_MUSIC_BLOCK_KEYWORDS = [
    "actor", "actress", "drama", "movie", "film", "netflix", "series",
    "documentary", "docu", "reality show", "variety show", "broadcast",
    "to star in", "cast", "casting", "growth documentary",
    "dating", "date", "breakup", "break up", "boyfriend", "girlfriend",
    "lover", "relationship", "couple", "marriage", "divorce", "rumor",
    "scandal", "kiss", "romance", "wedding", "pregnant",
    "brand reputation", "reputation rankings", "star brand reputation",
    "演员", "女演员", "男演员", "韩剧", "电视剧", "电影", "纪录片", "综艺",
    "出演", "主演", "参演", "选角", "恋情", "约会", "分手", "男友", "女友",
    "恋人", "情侣", "结婚", "离婚", "传闻", "绯闻", "恋爱", "品牌声誉", "声誉榜",
]

BAD_TOPIC_PATTERNS = [
    "artist tag", "all kpop all the time", "tag -", "/tag/",
    "category", "archive", "profile", "author",
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
        search_cfg = (config or {}).get("topic_agent.search", {})
        self.candidate_count = search_cfg.get("candidate_count", 5)
        self.selected_count = search_cfg.get("selected_count", 2)
        self.freshness_hours = search_cfg.get("freshness_hours", 24)
        self.min_images = search_cfg.get("min_images_per_article", 1)
        # 搜索结果缓存：同日内相同 query 不重复调用 Tavily
        self._search_cache: Dict[str, Tuple[float, List[Dict]]] = {}
        self._cache_ttl: int = 3600  # 缓存有效期 1 小时

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

        # 限制候选数量（评分成本考虑）
        if len(fresh_candidates) > self.candidate_count * 3:
            # 先用简单启发式排序，取top N
            fresh_candidates = sorted(
                fresh_candidates,
                key=lambda c: (self._hours_ago(c), -(c.get("score", 0) or 0))
            )[:self.candidate_count * 3]

        # Step 2.5: 顶流明星过滤 — 只保留提及顶流明星的文章
        fresh_candidates = self._filter_top_stars(fresh_candidates)
        logger.info(f"[选题Agent] 顶流明星过滤后: {len(fresh_candidates)} 篇")

        if not fresh_candidates:
            return AgentResult(
                status=AgentStatus.FAILED,
                agent_name=self.name,
                error="未找到提及顶流明星的候选文章",
            )

        # Step 2.6: 音乐相关性硬过滤
        # 只保留韩国男团/女团/成员的音乐动态；过滤影视、恋情、分手、演员八卦、聚合页。
        before_music_filter = len(fresh_candidates)
        fresh_candidates = self._filter_kpop_music_topics(fresh_candidates)
        logger.info(
            f"[选题Agent] K-pop音乐相关过滤: {before_music_filter} → {len(fresh_candidates)} 篇 "
            f"(排除 {before_music_filter - len(fresh_candidates)} 篇影视/恋情/分手/聚合页)"
        )

        if not fresh_candidates:
            return AgentResult(
                status=AgentStatus.FAILED,
                agent_name=self.name,
                error="未找到合格的K-pop音乐类候选文章；影视/恋情/分手/演员八卦已过滤",
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

        # Step 6: 选最高2篇
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

        search_sources = self.get_config("topic_agent.search_sources", [])
        general_keywords = self.get_config("topic_agent.general_keywords", [])

        all_results = []
        seen_urls = set()

        # 构建搜索查询列表
        search_queries = self._build_search_queries(search_sources, general_keywords)

        # 并行执行搜索（每次最多3个查询，避免API超限）
        batch_size = 3
        for i in range(0, len(search_queries), batch_size):
            batch = search_queries[i:i + batch_size]
            tasks = [self._tavily_search(q) for q in batch]
            results = await asyncio.gather(*tasks)

            for query_result in results:
                if not query_result:
                    continue
                for article in query_result:
                    url = article.get("url", "")
                    if url and url not in seen_urls:
                        seen_urls.add(url)
                        all_results.append(article)

        logger.info(f"[选题Agent] Tavily搜索完成: {len(all_results)} 篇独特文章")
        return all_results

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
        """执行单次 Tavily 搜索（含缓存，同 query 1小时内不重复调用）"""
        import time

        query = query_info["query"]
        source_name = query_info.get("source_name", "unknown")
        cache_key = f"{query}|{self.get_config('tavily.time_range', 'day')}"

        # 检查缓存
        now_ts = time.time()
        if cache_key in self._search_cache:
            cached_ts, cached_result = self._search_cache[cache_key]
            if now_ts - cached_ts < self._cache_ttl:
                logger.info(
                    f"[选题Agent] 🗄️ 缓存命中 | {source_name} | query='{query[:50]}' → {len(cached_result)} 篇（缓存年龄 {int((now_ts - cached_ts)/60)} 分钟）"
                )
                return cached_result

        api_key = self.get_config("tavily.api_key", "")
        base_url = self.get_config("tavily.base_url", "https://api.tavily.com")
        search_depth = self.get_config("tavily.search_depth", "advanced")
        max_results = min(self.get_config("tavily.max_results", 10), 3)  # 优化：每次最多3条，降低费用
        include_images = self.get_config("tavily.include_images", True)
        include_image_descriptions = self.get_config("tavily.include_image_descriptions", True)
        include_raw_content = self.get_config("tavily.include_raw_content", "markdown")  # 回退：写作需要 raw_content
        time_range = self.get_config("tavily.time_range", "24h")

        payload = {
            "query": query,
            "search_depth": search_depth,
            "max_results": max_results,
            "include_images": include_images,
            "include_image_descriptions": include_image_descriptions,
            "include_raw_content": include_raw_content,
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

    # ==================== 顶流明星过滤 ====================

    # 跳过聚合页/分类页 URL 模式（这类页面 raw_content 包含大量外链，导致误匹配）
    _SKIP_URL_PATTERNS = [
        "/category/", "/cat/", "/tag/", "/tags/",
        "/video/", "/videos/", "/playlist/",
        "/news/", "/archives/", "/archive/",
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

    def _filter_kpop_music_topics(self, candidates: List[Dict]) -> List[Dict]:
        """
        硬过滤：只保留韩国男团/女团/成员的音乐动态。
        规则：
        1. 标题/URL 明确是纪录片、影视、恋情、声誉榜、体育、商业新闻，直接过滤。
        2. 标题/URL 明确是专辑、回归、MV、概念照、舞台、演唱会、榜单，优先保留。
        3. 不用网页全文里的 cast / broadcast 等模板词误杀音乐文章。
        """
        filtered = []

        hard_block_title_keywords = [
            "brand reputation", "reputation rankings", "star brand reputation",
            "documentary", "growth documentary", "to star in",
            "actor", "actress", "drama", "movie", "film", "netflix", "series",
            "dating", "date", "breakup", "break up", "boyfriend", "girlfriend",
            "lover", "relationship", "couple", "marriage", "divorce", "rumor",
            "scandal", "kiss", "romance", "wedding", "pregnant",
            "football", "world cup", "semiconductor", "chip cluster",
            "品牌声誉", "声誉榜", "纪录片", "演员", "韩剧", "电视剧", "电影",
            "恋情", "约会", "分手", "男友", "女友", "恋人", "情侣",
            "结婚", "离婚", "传闻", "绯闻", "恋爱", "足球", "世界杯", "半导体",
        ]

        strong_music_title_keywords = [
            "comeback", "album", "single", "ep", "mini album", "full album",
            "music video", "mv", "teaser", "concept photo", "concept photos",
            "tracklist", "stage", "performance", "concert", "tour",
            "fanmeeting", "fan meeting", "showcase", "festival",
            "music bank", "inkigayo", "m countdown",
            "billboard", "world albums", "melon", "spotify", "chart",
            "debut", "release", "dance practice",
            "回归", "专辑", "单曲", "新歌", "MV", "音乐视频", "舞台",
            "打歌", "演唱会", "巡演", "见面会", "预告", "概念照",
            "音源", "榜单", "出道",
        ]

        for article in candidates:
            title = article.get("title", "") or ""
            url = article.get("url", "") or ""
            content = (
                article.get("content", "")
                or article.get("raw_content", "")
                or article.get("summary", "")
                or article.get("description", "")
                or ""
            )
            source = article.get("source", "") or article.get("source_name", "") or ""

            title_url = f"{title} {url}".lower()
            text_all = f"{title} {url} {content} {source}".lower()

            # 聚合页、标签页、作者页、档案页不做
            if any(p in title_url for p in BAD_TOPIC_PATTERNS):
                logger.info(f"[选题Agent] 🚫 跳过聚合页/标签页: {title[:80]} | {url[:80]}")
                continue

            # 标题/URL 明确是非音乐内容，直接过滤
            if any(k.lower() in title_url for k in hard_block_title_keywords):
                logger.info(f"[选题Agent] 🚫 跳过非音乐内容/影视恋情纪录片声誉榜体育商业: {title[:80]} | {url[:80]}")
                continue

            # 标题/URL 明确是音乐动态，直接保留
            strong_music_in_title = any(k.lower() in title_url for k in strong_music_title_keywords)

            # 如果标题不明显，再看全文是否有强音乐关键词
            strong_music_in_text = any(k.lower() in text_all for k in strong_music_title_keywords)

            if not (strong_music_in_title or strong_music_in_text):
                logger.info(f"[选题Agent] 🚫 跳过非强音乐动态: {title[:80]} | {url[:80]}")
                continue

            filtered.append(article)

        return filtered

    def _filter_top_stars(self, candidates: List[Dict]) -> List[Dict]:
        """过滤出真正关于顶流明星的文章

        匹配策略（避免侧边栏/推荐链接导致的误匹配）：
        1. 标题中命中顶流明星 → 直接通过（强信号）
        2. 内容中命中2个以上不同顶流明星 → 通过（多明星提及，可能是真正娱乐新闻）
        3. 内容中仅命中1个明星但标题不含娱乐关键词 → 拒绝（可能是非娱乐文章碰巧提及）
        """
        from src.topic.scoring import ScoringSystem

        # 非娱乐类文章标题关键词（出现这些关键词的文章直接拒绝）
        NON_ENTERTAINMENT_KEYWORDS = [
            "항공", "유류", "flight", "airline", "fuel surcharge",  # 航空
            "모바일", "mobile game", "게임 출시", "game launch",  # 游戏
            "증권", "주식", "stock", "financial", "earnings",  # 金融
            "부동산", "real estate", "property",  # 房产
            "선거", "election", "정치", "politics",  # 政治
            "스포츠", "sports", "축구", "야구",  # 体育
            "날씨", "weather", "기상",  # 天气
        ]

        filtered = []
        for c in candidates:
            url = c.get("url", "")
            title = (c.get("title") or "")
            content = (c.get("content") or "")
            raw = (c.get("raw_content") or "")
            title_lower = title.lower()
            content_text = content + " " + raw

            # 跳过聚合页/分类页（URL 含 /category/ /tag/ /video/ 等）
            if self._is_aggregate_page(url):
                logger.info(
                    f"[选题Agent] 跳过聚合页: '{title[:30]}' | {url[:60]}"
                )
                continue

            # 先检查是否为非娱乐文章
            is_non_entertainment = False
            for kw in NON_ENTERTAINMENT_KEYWORDS:
                if kw in title_lower:
                    is_non_entertainment = True
                    logger.info(
                        f"[选题Agent] 跳过非娱乐文章(标题含'{kw}'): '{title[:40]}'"
                    )
                    break

            if is_non_entertainment:
                continue

            # 检查标题中是否命中顶流明星（强信号）
            title_star_hits = []
            for star in ScoringSystem.TOP_STARS:
                if ScoringSystem._match_star(star, title):
                    title_star_hits.append(star)

            if title_star_hits:
                filtered.append(c)
                logger.info(
                    f"[选题Agent] ✅ 标题命中顶流明星 {title_star_hits}: '{title[:40]}'"
                )
                continue

            # 标题未命中，检查内容中命中多少个不同明星
            content_star_hits = set()
            for star in ScoringSystem.TOP_STARS:
                if ScoringSystem._match_star(star, content_text):
                    content_star_hits.add(star)

            if len(content_star_hits) >= 2:
                # 内容中命中2个以上不同明星，可能是真正娱乐新闻
                filtered.append(c)
                logger.info(
                    f"[选题Agent] ✅ 内容命中{len(content_star_hits)}个顶流明星: '{title[:40]}'"
                )
            else:
                logger.info(
                    f"[选题Agent] 跳过(标题未命中且内容仅命中{len(content_star_hits)}个明星): '{title[:40]}'"
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
        cutoff = now - timedelta(hours=self.freshness_hours)
        current_year = now.year

        fresh = []
        rejected_old = 0
        rejected_nodate = 0

        for c in candidates:
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
