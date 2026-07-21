"""
综合评分系统 - 15个评分字段

评分维度:
  total_score              - 总分(加权)
  topic_heat_score         - 话题热度
  freshness_score          - 时效性
  image_quality_score      - 图片质量
  image_relevance_score    - 图文相关性(重要)
  article_quality_score    - 文章质量
  predicted_read_score     - 预测阅读量
  risk_score               - 风险评估(图片来源风险)
  anti_ai_score            - 反AI检测分数
  selected_reason          - 选中理由
  risk_notes               - 风险备注
  image_quality_notes      - 图片质量备注
  duplicate_check_result   - 重复检测
  source_urls              - 来源URL列表
"""

import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.logger import logger


class ScoringSystem:
    """综合评分系统 - 对预选文章进行多维度评分"""

    # TOP 10 男团 + TOP 10 女团（及核心成员）
    # 搜索过滤 + 评分加权专用，不含演员/综艺/体育明星
    TOP_STARS = [
        # ============ TOP 10 男团 ============
        # 1. BTS (방탄소년단)
        "BTS", "방탄소년단", "RM", "Namjoon", "슈가", "Suga", "제이홉", "j-hope",
        "지민", "Jimin", "뷔", "V", "정국", "Jungkook", "진", "Jin",
        # 2. Stray Kids (스트레이키즈)
        "Stray Kids", "스트레이키즈", "방찬", "Bang Chan", "현진", "Hyunjin",
        "한", "Han", "펠릭스", "Felix", "승민", "Seungmin", "아이엔", "I.N.",
        # 3. SEVENTEEN (세븐틴)
        "SEVENTEEN", "세븐틴", "S.COUPS", "에스쿱스", "정한", "Jeonghan",
        "조슈아", "Joshua", "호시", "Hoshi", "원우", "Wonwoo", "우지", "Woozi",
        "민규", "Mingyu", "승관", "Seungkwan", "버논", "Vernon", "디노", "Dino",
        # 4. TXT / TOMORROW X TOGETHER
        "TXT", "TOMORROW X TOGETHER", "투모로우바이투게더",
        "연준", "Yeonjun", "수빈", "Soobin", "태현", "Taehyun", "휴닝카이", "Huening Kai",
        # 5. ENHYPEN (엔하이픈)
        "ENHYPEN", "엔하이픈", "정원", "Jungwon", "희승", "Heeseung",
        "제이", "Jay", "제이크", "Jake", "성훈", "Sunghoon", "선우", "Sunoo", "니키", "Niki",
        # 6. NCT (엔씨티)
        "NCT", "엔씨티", "NCT 127", "NCT DREAM", "WayV",
        "태일", "Taeil", "쟈니", "Johnny", "유타", "Yuta", "도영", "Doyoung",
        "쟈에민", "Jaemin", "해찬", "Haechan", "마크", "Mark",
        "텐", "Ten", "윈윈", "Winwin", "쟈성", "Jaehyun",
        # 7. EXO (엑소)
        "EXO", "엑소", "수호", "Suho", "찬열", "Chanyeol",
        "백현", "Baekhyun", "세훈", "Sehun", "첸", "Chen", "디오", "D.O.",
        # 8. TWS (투어스)
        "TWS", "투어스", "청하", "Kyungmin", "두훈", "Dohoon",
        "영재", "Youngjae", "한율", "Hanul", "지호", "Jiho", "신유", "Shinyu",
        # 9. RIIZE (라이즈)
        "RIIZE", "라이즈", "정우", "Jungwoo", "찬영", "Chanyoung",
        "소희", "Sohui", "건희", "Gunwook", "성현", "Sunghyun",
        # 10. BIGBANG (빅뱅)
        "BIGBANG", "빅뱅", "G-Dragon", "지드래곤", "태양", "Taeyang", "대성", "Daesung",

        # ============ TOP 10 女团 ============
        # 1. BLACKPINK (블랙핑크)
        "BLACKPINK", "블랙핑크", "지수", "Jisoo", "제니", "Jennie",
        "로제", "Rosé", "리사", "Lisa",
        # 2. NewJeans (뉴진스)
        "NewJeans", "뉴진스", "민지", "Minji", "한니", "Hanni",
        "다니엘", "Danielle", "해린", "Haerin", "혜인", "Hyein",
        # 3. aespa (에스파)
        "aespa", "에스파", "카리나", "Karina", "지젤", "Giselle",
        "윈터", "Winter", "닝닝", "Ningning",
        # 4. IVE (아이브)
        "IVE", "아이브", "가을", "Gaeul", "안유진", "An Yujin",
        "레이", "Rei", "장원영", "Jang Wonyoung", "이서", "Lee Seo",
        # 5. LE SSERAFIM (르세라핌)
        "LE SSERAFIM", "르세라핌", "김채원", "Kim Chaewon", "사쿠라", "Sakura",
        "허윤진", "Huh Yunjin", "김지민", "Kim Jiyun", "홍은채", "Hong Eunchae",
        # 6. TWICE (트와이스)
        "TWICE", "트와이스", "나연", "Nayeon", "정연", "Jeongyeon",
        "모모", "Momo", "사나", "Sana", "지효", "Jihyo",
        "미나", "Mina", "다현", "Dahyun", "채영", "Chaeyoung", "쯔위", "Tzuyu",
        # 7. (G)I-DLE ((여자)아이들)
        "(G)I-DLE", "(여자)아이들", "여원", "Miyeon", "수연", "Soyeon",
        "민니", "Minnie", "우기", "Woo!Gui", "슈화", "Shuhua",
        # 8. ITZY (있지)
        "ITZY", "있지", "예지", "Yeji", "리아", "Lia",
        "류진", "Ryujin", "채령", "Chaeryeong", "유나", "Yuna",
        # 9. STAYC (스테이씨)
        "STAYC", "스테이씨", "수민", "Sumin", "시은", "Sieun",
        "아이사", "Isa", "세은", "Seeun", "윤", "Yoon",
        # 10. NMIXX (엔믹스)
        "NMIXX", "엔믹스", "릴리", "Lily", "해원", "Haewon",
        "설윤", "Sullyoon", "배이", "Bae", "지우", "Jiwoo", "규진", "Kyujin",
    ]

    # 需要单词边界匹配的短英文名（≤4字符，或常见英文名）
    SHORT_ENGLISH_STARS = {
        # 1-2字符
        "BTS", "EXO", "NCT", "TWS", "TXT", "ITZY", "IVE", "V", "RM", "Jin",
        # 3-4字符常见名（子串匹配易误伤）
        "Han", "Bang", "Felix", "Jay", "Jake", "Niki", "Mark", "Xia", "Lay",
        "Kai", "Woo", "Min", "Cha", "Yu", "Rei", "Gae", "Hye", "Seo",
        "Yun", "ISA", "Bae", "Lia", "Sie", "Shy", "The8",
    }

    ARTIST_ENTITY_ALIASES = {
        "BTS": ["BTS", "방탄소년단", "防弹少年团", "防弹", "Bangtan"],
        "BTS_JIN": [
            "BTS Jin", "방탄소년단 진", "김석진",
            "Kim Seokjin", "金硕珍",
        ],
        "IVE": ["IVE", "아이브", "アイヴ", "爱芙"],
        "ITZY": ["ITZY", "있지"],
        "IDLE": ["i-dle", "(G)I-DLE", "여자아이들", "(여자)아이들", "아이들"],
        "STRAY_KIDS_HAN": [
            "Stray Kids Han", "SKZ Han", "스트레이키즈 한",
            "한지성", "Han Jisung", "HAN of Stray Kids",
            "Stray Kids member Han",
        ],
        "BIGBANG_DAESUNG": [
            "BIGBANG Daesung", "BIGBANG 대성", "빅뱅 대성",
            "Daesung", "Kang Daesung", "강대성",
        ],
        "ENHYPEN": ["ENHYPEN", "엔하이픈"],
        "ENHYPEN_JUNGWON": ["Jungwon", "정원", "양정원", "Yang Jungwon"],
        "ENHYPEN_HEESEUNG": ["Heeseung", "희승", "이희승", "Lee Heeseung"],
        "ENHYPEN_JAY": ["Jay", "제이", "박종성", "Park Jongseong"],
        "ENHYPEN_JAKE": ["Jake", "제이크", "심재윤", "Sim Jaeyun"],
        "ENHYPEN_SUNGHOON": ["Sunghoon", "성훈", "박성훈", "Park Sunghoon"],
        "ENHYPEN_SUNOO": ["Sunoo", "선우", "김선우", "Kim Sunoo"],
        "ENHYPEN_NIKI": ["Ni-ki", "Niki", "니키", "西村力"],
        "STRAY_KIDS": [
            "Stray Kids", "SKZ", "스트레이키즈", "스트레이 키즈", "스키즈",
        ],
        "KISS_OF_LIFE": [
            "KISS OF LIFE", "KIOF", "키스오브라이프", "키스 오브 라이프",
        ],
        "LE_SSERAFIM": ["LE SSERAFIM", "르세라핌"],
        "AESPA": ["aespa", "에스파"],
        "BLACKPINK": ["BLACKPINK", "블랙핑크"],
        "NMIXX": ["NMIXX", "엔믹스"],
        "ZEROBASEONE": ["ZEROBASEONE", "ZB1", "제로베이스원"],
        "BOYNEXTDOOR": ["BOYNEXTDOOR", "보이넥스트도어"],
        "ATEEZ": ["ATEEZ", "에이티즈"],
        "THE_BOYZ": ["THE BOYZ", "더보이즈"],
        "TREASURE": ["TREASURE", "트레저"],
        "82MAJOR": ["82MAJOR", "82메이저"],
        "CRAVITY": ["CRAVITY", "크래비티"],
        "P1HARMONY": ["P1Harmony", "피원하모니"],
        "TWS": ["TWS", "투어스"],
        "ILLIT": ["ILLIT", "아일릿"],
        "KATSEYE": ["KATSEYE", "캣츠아이"],
        "MEOVV": ["MEOVV", "미야오"],
        "CORTIS": ["CORTIS", "코르티스"],
        "ALLDAY_PROJECT": ["ALLDAY PROJECT", "올데이 프로젝트"],
        "NEWJEANS": ["NewJeans", "뉴진스"],
        "NEWJEANS_DANIELLE": ["Danielle", "다니엘"],
        "MIN_HEE_JIN": ["Min Hee-jin", "Min Heejin", "민희진", "闵熙珍"],
    }

    @classmethod
    def detect_artist_entities(cls, text: str) -> set:
        """将韩/英/中/日别名归一到同一艺人实体。"""
        entities = set()
        mapped_aliases = {
            alias.casefold()
            for aliases in cls.ARTIST_ENTITY_ALIASES.values()
            for alias in aliases
        }
        for entity, aliases in cls.ARTIST_ENTITY_ALIASES.items():
            if any(cls._match_star(alias, text) for alias in aliases):
                entities.add(entity)
        for star in cls.TOP_STARS:
            if (
                "BTS_JIN" in entities
                and star in {"Jin", "진"}
            ):
                continue
            if (
                star.casefold() not in mapped_aliases
                and cls._match_star(star, text)
            ):
                entities.add(star)
        return entities

    @classmethod
    def _match_star(cls, star: str, text: str) -> bool:
        """严格匹配艺人名；短团名需要词边界和 K-pop 上下文。"""
        import re

        kpop_context = (
            r"k-?pop|idol|hybe|bighit|sm(?: entertainment)?|"
            r"jyp(?: entertainment)?|yg(?: entertainment)?|starship|ador|"
            r"source music|girl group|boy group|comeback|music video|\bmv\b|"
            r"teaser|album|concert|fans?|netizens?|airport|fashion week|"
            r"music show|member|wonyoung|yujin|gaeul|\brei\b|\bliz\b|leeseo|"
            r"taehyung|jungkook|jimin|suga|\bjin\b|jennie|lisa|karina|"
            r"回归|组合|成员|巡演|演唱会|音乐节目|爱豆|"
            r"월드투어|공연|매진|공항|출국|입국|음악방송|콘서트"
        )

        if star == "V":
            return bool(re.search(
                r"(?:\bBTS(?:'s)?\s+V\b|\bV\s+of\s+BTS\b|"
                r"\bKim\s+Taehyung\b|\bTaehyung\b)",
                text,
                flags=re.IGNORECASE,
            ))

        if star == "Ten":
            return bool(re.search(
                r"(?:\bNCT\s+Ten\b|\bWayV\s+Ten\b|\bTen\s+Lee\b|"
                r"\bChittaphon(?:\s+Leechaiyapornkul)?\b)",
                text,
                flags=re.IGNORECASE,
            ))

        if star in {"한", "Han"}:
            return bool(re.search(
                r"(?:\bStray\s+Kids(?:\s+member)?\s+Han\b|"
                r"\bSKZ\s+Han\b|\bHAN\s+of\s+Stray\s+Kids\b|"
                r"스트레이키즈\s*한(?![가-힣])|한지성|"
                r"\bHan\s+Jisung\b)",
                text,
                flags=re.IGNORECASE,
            ))

        if star in {"대성", "Daesung"}:
            if re.search(r"대성(?:황|공|리|동)", text):
                return False
            return bool(re.search(
                r"(?:BIGBANG\s+(?:대성|Daesung)|빅뱅\s+대성|"
                r"\bKang\s+Daesung\b|\bDaesung\b|강대성|"
                r"(?<![가-힣])대성(?![가-힣]).{0,20}"
                r"(?:빅뱅|멤버|솔로|콘서트|컴백)|"
                r"(?:빅뱅|멤버|솔로|콘서트|컴백).{0,20}"
                r"(?<![가-힣])대성(?![가-힣]))",
                text,
                flags=re.IGNORECASE,
            ))

        if star == "IVE":
            # 保留大小写以排除 Jony Ive、I've、Ive-designed。
            has_name = bool(re.search(r"(?<![A-Za-z])IVE(?:'s)?(?![A-Za-z-])", text))
            return has_name and bool(re.search(kpop_context, text, re.IGNORECASE))

        if star in {"TXT", "NCT", "ITZY", "RIIZE"}:
            has_name = bool(re.search(
                rf"(?<![A-Za-z0-9]){re.escape(star)}(?:'s)?(?![A-Za-z0-9-])",
                text,
            ))
            return has_name and bool(re.search(kpop_context, text, re.IGNORECASE))

        star_lower = star.lower()
        text_lower = text.lower()

        if re.search(r"[A-Za-z]", star):
            # 所有拉丁字母艺人名都使用严格边界，避免 Ten→attends、
            # Ive→creative 等普通单词子串误判。
            pattern = (
                r"(?<![A-Za-z0-9])"
                + re.escape(star_lower)
                + r"(?![A-Za-z0-9])"
            )
            return bool(re.search(pattern, text_lower))

        # 韩文名允许常见助词，但不能匹配普通词内部。
        particles = r"(?:은|는|이|가|을|를|의|도|과|와|로|으로)?"
        return bool(re.search(
            rf"(?<![가-힣]){re.escape(star)}{particles}(?![가-힣])", text
        ))

    # 明确排除的低热度关键词（出现这些的文章降分）
    LOW_HEAT_KEYWORDS = [
        "trainee", "연습생", "audition", "오디션",
        "unknown", "debut soon", "예정",
        "pre-debut", "사전데뷔",
    ]

    KOREAN_MEDIA_DOMAINS = {
        "entertain.naver.com", "osen.co.kr", "newsen.com",
        "starnewskorea.com", "xportsnews.com", "mydaily.co.kr",
        "dispatch.co.kr", "tenasia.hankyung.com", "sports.chosun.com",
        "tvreport.co.kr", "mk.co.kr", "heraldpop.com", "imbc.com",
    }
    CONCRETE_EVENT_KEYWORDS = [
        "comeback", "mv", "music video", "teaser", "concept photo",
        "single", "album", "mini album", "stage", "music show",
        "concert", "tour", "airport", "fashion week", "brand event",
        "livestream", "live broadcast", "instagram", "social media",
        "fans react", "netizens", "controversy", "agency response",
        "legal action", "컴백", "뮤직비디오", "티저", "콘서트",
        "공항", "패션위크", "브랜드", "라이브", "입장",
    ]
    MEDIUM_EVENT_KEYWORDS = [
        "quiz show", "variety reference", "mentioned on", "overseas media",
        "해외 방송", "퀴즈쇼", "예능 언급",
    ]
    ESPORTS_GAME_KEYWORDS = [
        "pubg", "pnc", "e스포츠", "이스포츠", "배그", "국가대항전",
        "크래프톤", "덕지순례", "게임", "선수", "경기", "대회",
    ]
    APPROVED_GROUP_ENTITIES = {
        "KISS_OF_LIFE", "NMIXX", "ZEROBASEONE", "BOYNEXTDOOR", "NCT",
        "ATEEZ", "THE_BOYZ", "TREASURE", "82MAJOR", "CRAVITY",
        "P1HARMONY", "TWS", "ILLIT", "KATSEYE", "MEOVV", "CORTIS",
        "ALLDAY_PROJECT",
    }
    MACRO_ANALYSIS_KEYWORDS = [
        "generation shift", "k-pop generation", "big 4", "big four",
        "successor list", "industry analysis", "industry trend",
        "market outlook", "世代交替", "四大公司", "接班人",
        "行业分析", "行业趋势", "格局分析", "名单盘点",
    ]

    def __init__(self, config: Dict):
        self.config = config
        self.scoring_cfg = config.get("topic_agent", {}).get("scoring", {})
        self.weights = self.scoring_cfg.get("weights", {})
        self.consistency_threshold = 40  # 硬编码低阈值测试（配置读取有问题）
        self.duplicate_days = self.scoring_cfg.get("duplicate_check_days", 7)

    async def score_article(
        self,
        article: Dict,
        tavily_images: List[Dict],
        llm_client=None,
    ) -> Dict:
        """
        对单篇文章进行综合评分

        Args:
            article: Tavily 搜索返回的文章
            tavily_images: 该文章关联的图片列表
            llm_client: OpenAI 客户端(用于LLM评分)

        Returns:
            包含全部15个评分字段的字典
        """
        # 调试：打印阈值
        logger.info(f"[评分] image_consistency_threshold={self.consistency_threshold}")
        # 1. 话题热度
        topic_heat = self._score_topic_heat(article)

        # 2. 时效性
        freshness = self._score_freshness(article)

        # 3. 图片质量
        image_quality, image_quality_notes = self._score_image_quality(tavily_images)

        # 4. 图文相关性（重要评分点）
        image_relevance = self._score_image_relevance(article, tavily_images)

        # 5. 文章质量
        article_quality = self._score_article_quality(article)

        # 6. 预测阅读量
        predicted_read = self._score_predicted_read(article, topic_heat, freshness)

        # 7. 风险评估（图片来源风险）
        risk, risk_notes = self._score_risk(tavily_images, article)

        # 8. 反AI检测分数
        anti_ai = self._score_anti_ai(article)

        # 9. 重复检测
        duplicate_result = self._check_duplicate(article)

        # 10. 来源URL
        source_urls = self._extract_source_urls(article, tavily_images)

        # 加权总分
        total_score = self._calculate_total(
            topic_heat, freshness, image_quality, image_relevance,
            article_quality, predicted_read, risk, anti_ai
        )
        source_priority, editorial_adjustment, editorial_notes = (
            self._score_editorial_priority(article, tavily_images)
        )
        total_score = max(0, min(10, total_score + editorial_adjustment))
        # 同题不同来源不是硬重复：允许作为补充候选，但轻度降权，
        # 避免连续发布同质内容。完全相同 URL 仍由 ready 检查硬过滤。
        if duplicate_result.get("related_event"):
            total_score = max(0, total_score - 0.5)

        # 选中理由
        selected_reason = self._generate_reason(
            total_score, topic_heat, freshness, image_relevance, risk
        )

        result = {
            "total_score": round(total_score, 2),
            "topic_heat_score": round(topic_heat, 2),
            "freshness_score": round(freshness, 2),
            "image_quality_score": round(image_quality, 2),
            "image_relevance_score": round(image_relevance, 2),
            "article_quality_score": round(article_quality, 2),
            "predicted_read_score": round(predicted_read, 2),
            "risk_score": round(risk, 2),
            "anti_ai_score": round(anti_ai, 2),
            "selected_reason": selected_reason,
            "risk_notes": risk_notes,
            "image_quality_notes": image_quality_notes,
            "duplicate_check_result": duplicate_result,
            "source_urls": source_urls,
            "source_priority_score": source_priority,
            "editorial_priority_notes": editorial_notes,
        }

        logger.info(
            f"[评分] 文章='{article.get('title','?')[:40]}' "
            f"总分={result['total_score']} "
            f"热度={topic_heat:.1f} 时效={freshness:.1f} "
            f"图质={image_quality:.1f} 图文={image_relevance:.1f} "
            f"风险={risk:.1f}"
        )

        return result

    def _score_editorial_priority(
        self, article: Dict, images: List[Dict]
    ) -> Tuple[float, float, str]:
        """优先韩媒、具体艺人事件和可直接使用的图片，降权宏观分析。"""
        url = (article.get("url") or "").lower()
        title_summary = " ".join([
            article.get("title") or "",
            article.get("content") or "",
        ]).lower()
        is_korean_media = any(domain in url for domain in self.KOREAN_MEDIA_DOMAINS)
        has_event = any(
            keyword in title_summary for keyword in self.CONCRETE_EVENT_KEYWORDS
        )
        macro_hit = next(
            (keyword for keyword in self.MACRO_ANALYSIS_KEYWORDS
             if keyword in title_summary),
            None,
        )
        esports_hit = next(
            (keyword for keyword in self.ESPORTS_GAME_KEYWORDS
             if keyword in title_summary),
            None,
        )
        medium_event = any(
            keyword in title_summary for keyword in self.MEDIUM_EVENT_KEYWORDS
        )
        has_source_metadata = bool(
            article.get("original_title")
            and article.get("source_url")
            and article.get("published_at")
        )

        adjustment = 0.0
        notes = []
        if is_korean_media:
            adjustment += 1.2
            notes.append("韩国本土媒体")
        if len(images) >= 2:
            adjustment += 0.6
            notes.append(f"原文图片{len(images)}张")
        else:
            adjustment -= 1.0
            notes.append("原文图片不足")
        if has_event:
            adjustment += 0.8
            notes.append("具体艺人事件")
        else:
            adjustment -= 1.0
            notes.append("缺少具体事件")
        if medium_event and not has_event:
            adjustment += 0.2
            notes.append("海外节目/媒体提及")
        if has_source_metadata:
            adjustment += 0.4
            notes.append("来源元数据完整")
        if macro_hit:
            adjustment -= 3.0
            notes.append(f"宏观分析:{macro_hit}")
        if esports_hit:
            adjustment -= 6.0
            notes.append(f"电竞/游戏:{esports_hit}")

        priority = (
            (4 if is_korean_media else 0)
            + (3 if has_event else 0)
            + (2 if len(images) >= 2 else 0)
            + (1 if has_source_metadata else 0)
        )
        return priority, adjustment, "；".join(notes)

    def _score_topic_heat(self, article: Dict) -> float:
        """话题热度评分 (0-10) — 优先韩国顶流明星"""
        score = 3.0  # 降低基础分，只有顶流明星能拿高分

        title = (article.get("title") or "")
        content = (article.get("content") or "")
        raw = (article.get("raw_content") or "")
        full_text = title + " " + content + " " + raw

        # 核心：检查是否包含顶流明星名字
        title_hits = 0
        content_hits = 0
        for star in self.TOP_STARS:
            if self._match_star(star, title):
                title_hits += 1
                score += 2.5  # 标题中出现 = 文章主要关于该明星
            elif self._match_star(star, full_text):
                content_hits += 1
                score += 0.8  # 内容中出现 = 可能只是提及

        total_hits = title_hits + content_hits
        approved_hits = (
            self.detect_artist_entities(title) & self.APPROVED_GROUP_ENTITIES
        )
        if approved_hits and title_hits == 0:
            title_hits += 1
            total_hits += 1
            score += 2.5
            logger.info(
                f"[评分] 备用K-pop组合命中: {sorted(approved_hits)}"
            )
        if total_hits > 0:
            logger.info(
                f"[评分] 🔥 顶流明星命中: 标题{title_hits} 内容{content_hits} | "
                f"文章='{title[:40]}'"
            )
        else:
            # 没有命中任何顶流明星，大幅降分
            score -= 3.0
            logger.info(
                f"[评分] ⚠️ 未命中顶流明星 | 文章='{title[:40]}'"
            )

        # 通用热门事件关键词加权
        hot_keywords = [
            "comeback", "debuts", "new album", "tour", "concert",
            "award", "win", "mama", "mnet", "music bank", "inkigayo",
            "brand ambassador", "fashion week", "met gala",
            "컴백", "데뷔", "콘서트", "시상식", "브랜드",
            "sns", "instagram", "update", "근황", "화보",
            "vip premiere", "시사회", "press conference", "기자회견",
            "dating", "열애", "wedding", "결혼",
        ]
        text_lower = full_text.lower()
        for kw in hot_keywords:
            if kw in text_lower:
                score += 0.5

        # 低热度关键词降分
        for kw in self.LOW_HEAT_KEYWORDS:
            if kw in text_lower:
                score -= 1.0
                logger.info(f"[评分] ⬇️ 低热度关键词 '{kw}' 命中，降分")

        # Tavily score 转化
        tavily_score = article.get("score", 0)
        if tavily_score > 0:
            score += min(tavily_score * 5, 2.0)

        return max(0, min(score, 10.0))

    def _score_freshness(self, article: Dict) -> float:
        """时效性评分 (0-10) - 必须在24小时内"""
        published = article.get("published_date")
        now = datetime.now()

        if not published:
            # 无发布时间，根据 Tavily 返回判断
            return 4.0

        if isinstance(published, str):
            try:
                published = datetime.fromisoformat(published.replace("Z", "+00:00"))
                if published.tzinfo:
                    published = published.replace(tzinfo=None)
            except (ValueError, TypeError):
                return 4.0

        hours_ago = (now - published).total_seconds() / 3600

        if hours_ago <= 6:
            return 10.0
        elif hours_ago <= 12:
            return 8.5
        elif hours_ago <= 18:
            return 7.0
        elif hours_ago <= 24:
            return 5.5
        elif hours_ago <= 48:
            return 3.0
        else:
            return 1.0

    def _score_image_quality(
        self, images: List[Dict]
    ) -> Tuple[float, str]:
        """图片质量评分 (0-10) + 备注"""
        if not images:
            return 0.0, "无图片可用"

        score = 5.0
        notes_parts = []

        # 图片数量
        img_count = len(images)
        if img_count >= 4:
            score += 2.0
            notes_parts.append(f"图片充足({img_count}张)")
        elif img_count >= 2:
            score += 1.0
            notes_parts.append(f"图片数量适中({img_count}张)")
        else:
            score -= 1.0
            notes_parts.append(f"图片不足(仅{img_count}张)")

        # 图片描述完整性
        has_desc = sum(1 for img in images if img.get("description"))
        if has_desc >= img_count * 0.5:
            score += 1.0
            notes_parts.append(f"{has_desc}张有描述")
        else:
            notes_parts.append("多数图片无描述")

        # 排除广告图/banner/logo 后的有效图片
        risk_filter = self.config.get("template_system.image_risk_filter", {})
        excluded_patterns = risk_filter.get("excluded_url_patterns", [])
        valid_images = 0
        for img in images:
            url = img.get("url", "").lower() if isinstance(img, dict) else str(img).lower()
            is_excluded = any(p in url for p in excluded_patterns)
            if not is_excluded:
                valid_images += 1

        if valid_images < img_count:
            excluded_count = img_count - valid_images
            score -= excluded_count * 0.5
            notes_parts.append(f"排除{excluded_count}张广告/banner图")

        if valid_images == 0:
            score = 0.0
            notes_parts.append("无有效爱豆人物图")

        score = max(0, min(score, 10.0))
        return score, "; ".join(notes_parts)

    def _score_image_relevance(self, article: Dict, images: List[Dict]) -> float:
        """图文相关性评分 (0-10) - 重要评分点"""
        if not images:
            return 0.0

        score = 5.0

        title = (article.get("title") or "").lower()
        content = ((article.get("content") or "") or (article.get("raw_content") or "")).lower()[:500]

        # 图片与文章来自同一搜索结果（同源加分）
        for img in images:
            if isinstance(img, dict):
                img_source = (img.get("source") or "").lower()
                img_desc = (img.get("description") or "").lower()

                # 图片描述与文章标题的关键词重叠
                if img_desc:
                    title_words = set(re.findall(r'[a-z가-힣]+', title))
                    desc_words = set(re.findall(r'[a-z가-힣]+', img_desc))
                    overlap = len(title_words & desc_words)
                    if overlap > 0:
                        score += min(overlap * 0.5, 2.0)

        # 图片来自文章自身URL（最高相关性）
        article_url = article.get("url", "").lower()
        same_source_count = sum(
            1 for img in images
            if isinstance(img, dict) and img.get("source", "").lower() == article_url
        )
        if same_source_count > 0:
            score += 2.0

        return min(score, 10.0)

    def _score_article_quality(self, article: Dict) -> float:
        """文章质量评分 (0-10)"""
        score = 5.0

        content = article.get("content", "") or article.get("raw_content", "")
        content_len = len(content)

        # 内容长度
        if content_len > 2000:
            score += 2.0
        elif content_len > 1000:
            score += 1.0
        elif content_len > 500:
            score += 0.5
        else:
            score -= 1.0

        # 标题质量
        title = article.get("title", "")
        if 10 <= len(title) <= 50:
            score += 1.0

        # 有明确来源
        if article.get("url"):
            score += 0.5

        # Tavily 相关性分数
        tavily_score = article.get("score", 0)
        if tavily_score > 0.5:
            score += 1.0

        return min(score, 10.0)

    def _score_predicted_read(
        self, article: Dict, heat: float, freshness: float
    ) -> float:
        """预测阅读量评分 (0-10)"""
        # 基于热度和时效性预测
        score = (heat * 0.5 + freshness * 0.3)

        title = article.get("title", "").lower()

        # 高点击率关键词
        click_keywords = [
            "new", "first", "rare", "exclusive", "behind", "unseen",
            "신곡", "최초", "단독", "공개",
            "comeback", "debut", "surprise", "viral",
        ]
        for kw in click_keywords:
            if kw in title:
                score += 0.5
                break

        return min(score, 10.0)

    def _score_risk(
        self, images: List[Dict], article: Dict
    ) -> Tuple[float, str]:
        """风险评估评分 (0-10, 分越高风险越低) + 风险备注"""
        score = 8.0
        notes_parts = []

        risk_filter = self.config.get("template_system.image_risk_filter", {})
        excluded_patterns = risk_filter.get("excluded_url_patterns", [])

        # 检查图片来源风险
        risky_images = 0
        for img in images:
            url = img.get("url", "").lower() if isinstance(img, dict) else str(img).lower()
            for pattern in excluded_patterns:
                if pattern in url:
                    risky_images += 1
                    notes_parts.append(f"可疑图片URL含'{pattern}'")
                    break

        if risky_images > 0:
            risk_ratio = risky_images / max(len(images), 1)
            score -= risk_ratio * 5.0
            notes_parts.append(f"{risky_images}张图片来源存在风险")

        # 检查是否有真实人物图
        valid_count = len(images) - risky_images
        if valid_count == 0:
            score = 0.0
            notes_parts.append("无真实爱豆人物图，高风险")

        # 检查文章来源可信度
        source_url = article.get("url", "").lower()
        trusted_domains = [
            "soompi.com", "allkpop.com", "koreaboo.com",
            "naver.com", "osen.co.kr", "xportsnews.com",
            "dispatch.co.kr", "sbsstar.net", "starnewskorea.com",
            "tenasia.hankyung.com", "koreaherald.com",
        ]
        is_trusted = any(d in source_url for d in trusted_domains)
        if is_trusted:
            score += 1.0
        else:
            score -= 1.0
            notes_parts.append("文章来源非主流韩国娱乐媒体")

        score = max(0, min(score, 10.0))

        if not notes_parts:
            notes_parts.append("无风险")

        return score, "; ".join(notes_parts)

    def _score_anti_ai(self, article: Dict) -> float:
        """反AI检测评分 (0-10) - 评估改写后能否通过AI检测"""
        score = 6.0

        content = article.get("content", "") or ""
        raw_content = article.get("raw_content", "") or ""

        # 源素材越丰富，改写后越自然
        total_content = content + raw_content
        if len(total_content) > 3000:
            score += 2.0
        elif len(total_content) > 1500:
            score += 1.0

        # 多源素材（有多个参考文章）更容易写出非AI感
        # 这个在批量评分时由调用方补充

        return min(score, 10.0)

    def _check_duplicate(self, article: Dict) -> Dict:
        """重复检测"""
        title = article.get("title", "").strip().lower()[:50]
        url = article.get("url", "")

        # 检查本地历史记录
        data_dir = Path(self.config.get("project_root", ".")) / "data" / "topics"
        is_duplicate = False
        duplicate_source = ""
        related_event = False

        if data_dir.exists():
            cutoff = datetime.now() - timedelta(days=self.duplicate_days)
            for history_file in sorted(data_dir.glob("*.json"), reverse=True)[:self.duplicate_days]:
                try:
                    file_date = datetime.strptime(history_file.stem, "%Y-%m-%d")
                    if file_date < cutoff:
                        break

                    with open(history_file, "r", encoding="utf-8") as f:
                        records = json.load(f)

                    for record in records:
                        for topic in record.get("topics", []):
                            past_title = topic.get("title", "").strip().lower()[:50]
                            past_url = topic.get("url", "")

                            if url and past_url == url:
                                is_duplicate = True
                                duplicate_source = past_url
                                break
                            if title and past_title == title:
                                related_event = True
                                duplicate_source = past_url or past_title
                        if is_duplicate:
                            break
                    if is_duplicate:
                        break
                except (json.JSONDecodeError, ValueError):
                    continue

        return {
            "is_duplicate": is_duplicate,
            "duplicate_source": duplicate_source,
            "related_event": related_event and not is_duplicate,
            "checked_days": self.duplicate_days,
            "status": (
                "duplicate" if is_duplicate
                else "related_event" if related_event
                else "unique"
            ),
        }

    def _extract_source_urls(self, article: Dict, images: List[Dict]) -> List[str]:
        """提取所有来源URL"""
        urls = []

        # 文章URL
        if article.get("url"):
            urls.append(article["url"])

        # 图片来源URL
        for img in images:
            if isinstance(img, dict):
                source = img.get("source", "")
                if source and source != "tavily_top" and source not in urls:
                    urls.append(source)

        return urls

    def _calculate_total(
        self, topic_heat, freshness, image_quality, image_relevance,
        article_quality, predicted_read, risk, anti_ai
    ) -> float:
        """加权总分计算"""
        w = self.weights

        total = (
            topic_heat * w.get("topic_heat_score", 0.15) +
            freshness * w.get("freshness_score", 0.15) +
            image_quality * w.get("image_quality_score", 0.10) +
            image_relevance * w.get("image_relevance_score", 0.20) +
            article_quality * w.get("article_quality_score", 0.10) +
            predicted_read * w.get("predicted_read_score", 0.10) +
            risk * w.get("risk_score", 0.10) +
            anti_ai * w.get("anti_ai_score", 0.10)
        )

        return total

    def _generate_reason(
        self, total, heat, freshness, image_relevance, risk
    ) -> str:
        """生成选中理由"""
        parts = []

        if total >= 7.0:
            parts.append("综合评分优秀")
        elif total >= 5.0:
            parts.append("综合评分良好")
        else:
            parts.append("综合评分一般")

        if heat >= 7:
            parts.append("话题热度高")
        if freshness >= 8:
            parts.append("时效性强(24h内)")
        if image_relevance >= 7:
            parts.append("图文相关性强")
        elif image_relevance < 4:
            parts.append("图文相关性不足")
        if risk < 4:
            parts.append("图片来源存在风险")
        elif risk >= 8:
            parts.append("图片来源安全")

        return "，".join(parts)

    def is_ready_for_draft(self, score_result: Dict) -> Tuple[bool, str]:
        """检查文章是否可以进入草稿"""
        # 重复检测
        dup = score_result.get("duplicate_check_result", {})
        if dup.get("is_duplicate"):
            return False, f"重复文章: {dup.get('duplicate_source', '')}"

        # 话题热度检查：必须达到最低阈值（确保是顶流明星相关）
        topic_heat = score_result.get("topic_heat_score", 0)
        if topic_heat < 4.0:
            return False, f"话题热度不足({topic_heat:.1f} < 4.0)，非顶流明星"

        # 图文相关性检查
        image_relevance = score_result.get("image_relevance_score", 0)
        if image_relevance < self.consistency_threshold / 10:
            return False, f"图文相关性不足({image_relevance:.1f} < {self.consistency_threshold / 10:.1f})"

        # 风险检查
        risk = score_result.get("risk_score", 0)
        if risk < 3.0:
            return False, f"风险过高({risk:.1f})"

        # 图片质量
        image_quality = score_result.get("image_quality_score", 0)
        if image_quality < 2.0:
            return False, f"图片质量过低({image_quality:.1f})"

        return True, "通过所有检查"
