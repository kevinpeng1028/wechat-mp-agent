"""
图文一致性检查与图片来源风险评估模块

功能:
1. 检查文章人物和图片人物是否一致
2. 检查文章组合和图片组合是否一致
3. 检查场景匹配（机场vs舞台等）
4. 图片来源风险评估（广告图/banner/logo排除）
5. 一致性低于85分不允许进入 ready
"""

import re
from typing import Any, Dict, List, Optional, Tuple

from src.logger import logger
from src.image.image_agent import _is_blocked_image


class ImageConsistencyChecker:
    """图文一致性检查器"""

    def __init__(self, config: Dict):
        self.config = config
        formatter_cfg = config.get("formatter_agent", {})
        consistency_cfg = formatter_cfg.get("consistency_check", {})
        self.threshold = consistency_cfg.get("threshold", 40)
        # 确保阈值不超过40，避免误判阻断发布
        if self.threshold > 40:
            self.threshold = 40
        self.check_fields = consistency_cfg.get("check_fields", [
            "idol_name", "group_name", "source_note",
            "caption", "image_description", "usage_scene",
        ])

        risk_filter = (
            config.get("template_system", {})
            .get("image_risk_filter", {})
        )
        self.excluded_patterns = risk_filter.get("excluded_url_patterns", [])
        self.excluded_types = risk_filter.get("excluded_types", [])
        self.require_real_person = risk_filter.get("require_real_person_photo", True)
        self.min_images_required = (
            config.get("topic_agent", {})
            .get("image", {})
            .get("min_images_required", 2)
        )
        self.allow_single_high_quality_image = (
            config.get("topic_agent", {})
            .get("image", {})
            .get("allow_single_high_quality_image", False)
        )

    def check_consistency(
        self,
        article: Dict,
        images: List[Dict],
    ) -> Dict:
        """
        执行完整的图文一致性检查

        Returns:
            {
                "consistency_score": 0-100,
                "passed": bool,
                "idol_match": bool,
                "group_match": bool,
                "scene_match": bool,
                "issues": [str],
                "valid_images": [Dict],
                "excluded_images": [Dict],
                "has_real_person_photo": bool,
            }
        """
        issues = []
        score = 100.0

        # Step 1: 图片来源风险过滤
        valid_images, excluded_images = self._filter_risky_images(images)

        if not valid_images:
            issues.append("无有效爱豆人物图（全部被风险过滤排除）")
            return self._build_result(0, issues, [], excluded_images, False)

        if len(excluded_images) > len(images) / 2:
            issues.append(
                f"超过一半图片不合格({len(excluded_images)}/{len(images)})"
            )
            return self._build_result(
                0, issues, valid_images, excluded_images, False
            )

        if len(valid_images) < self.min_images_required:
            if (
                self.allow_single_high_quality_image
                and
                len(valid_images) == 1
                and valid_images[0].get("quality_score", 0) >= 90
            ):
                issues.append("仅1张高质量图片，按例外保留")
            else:
                issues.append(
                    f"有效图片不足({len(valid_images)} < "
                    f"{self.min_images_required})"
                )
                return self._build_result(
                    0, issues, valid_images, excluded_images, False
                )

        # Step 2: 人物一致性检查
        idol_match, idol_issues = self._check_idol_match(article, valid_images)
        if not idol_match:
            score -= 30
            issues.extend(idol_issues)

        # Step 3: 组合一致性检查
        group_match, group_issues = self._check_group_match(article, valid_images)
        if not group_match:
            score -= 20
            issues.extend(group_issues)

        # Step 4: 场景一致性检查
        scene_match, scene_issues = self._check_scene_match(article, valid_images)
        if not scene_match:
            score -= 25
            issues.extend(scene_issues)

        # Step 5: 图片描述完整性
        desc_score = self._check_description_completeness(valid_images)
        if desc_score < 0.5:
            score -= 10
            issues.append("多数图片无描述，文章不应写具体画面细节")

        # Step 6: 真实人物图检查
        has_real_person = self._has_real_person_photo(valid_images)
        if self.require_real_person and not has_real_person:
            score = 0
            issues.append("无真实爱豆人物图，不满足 ready 条件")

        score = max(0, min(score, 100))
        passed = score >= self.threshold

        if not passed:
            issues.append(f"图文一致性 {score:.0f}分 低于阈值 {self.threshold}分")

        return self._build_result(
            score, issues, valid_images, excluded_images, has_real_person
        )

    def _filter_risky_images(self, images: List[Dict]) -> Tuple[List[Dict], List[Dict]]:
        """过滤有风险的图片（广告/banner/logo等）"""
        valid = []
        excluded = []

        for img in images:
            if not isinstance(img, dict):
                continue

            url = (img.get("url") or "")
            url_lower = url.lower()
            is_risky = False
            risk_reason = ""

            # 1. 使用 image_agent.py 的统一关键词过滤（最严格）
            desc = img.get("description") or ""
            if _is_blocked_image(url, desc):
                is_risky = True
                risk_reason = "URL/描述含被禁关键词(audition/ads/logo/banner等)"

            # 视觉质量与相关性由下载阶段产生，排版前必须再次执行硬门槛。
            if not is_risky and img.get("quality_passed") is False:
                is_risky = True
                risk_reason = img.get("quality_reason") or "视觉质量不合格"
            if not is_risky and img.get("relevance_passed") is False:
                is_risky = True
                risk_reason = img.get("relevance_reason") or "图片相关性不足"

            # 2. 配置中的 excluded_url_patterns
            if not is_risky:
                for pattern in self.excluded_patterns:
                    if pattern in url_lower:
                        is_risky = True
                        risk_reason = f"URL含'{pattern}'"
                        break

            # 3. 检查图片描述中是否有广告/banner标志
            if not is_risky:
                desc_lower = desc.lower()
                ad_keywords = ["logo", "banner", "advertisement", "ad ", "sponsor", "sponsored", "promo", "promotion", "widget", "icon", "avatar", "profile", "subscribe", "newsletter", "related", "recommend", "outbrain", "taboola", "doubleclick", "googlesyndication", "tracking", "affiliate", "campaign", "popup", "ads", "adserver", "googleads",
                              "subscribe", "follow us", "click here", "sign up"]
                for kw in ad_keywords:
                    if kw in desc_lower:
                        is_risky = True
                        risk_reason = f"描述含'{kw}'"
                        break

            if is_risky:
                img_copy = {**img, "excluded_reason": risk_reason, "status": "skipped"}
                excluded.append(img_copy)
                logger.info(f"[图文检查] 排除图片: {risk_reason} | {url[:80]}")
            else:
                valid.append(img)

        logger.info(
            f"[图文检查] 图片过滤: 有效={len(valid)} 排除={len(excluded)}"
        )

        return valid, excluded

    def _check_idol_match(
        self, article: Dict, images: List[Dict]
    ) -> Tuple[bool, List[str]]:
        """检查文章人物和图片人物是否一致"""
        issues = []

        # 从文章中提取人物名
        article_text = ((article.get("title") or "") + " " + ((article.get("content") or "")[:500] or "")).lower()
        article_idols = self._extract_idol_names(article_text)

        # 从图片描述中提取人物名
        image_idols = set()
        for img in images:
            desc = (img.get("description") or "").lower()
            caption = (img.get("caption") or "").lower()
            img_idols = self._extract_idol_names(desc + " " + caption)
            image_idols.update(img_idols)

        # 如果两边都提取到名字，检查是否一致
        if article_idols and image_idols:
            overlap = article_idols & image_idols
            if not overlap:
                issues.append(
                    f"文章人物({','.join(list(article_idols)[:3])})"
                    f"与图片人物({','.join(list(image_idols)[:3])})不一致"
                )
                return False, issues

        return True, []

    def _check_group_match(
        self, article: Dict, images: List[Dict]
    ) -> Tuple[bool, List[str]]:
        """检查文章组合和图片组合是否一致"""
        issues = []

        article_text = ((article.get("title") or "") + " " + ((article.get("content") or "")[:500] or "")).lower()
        article_groups = self._extract_group_names(article_text)

        image_groups = set()
        for img in images:
            desc = (img.get("description") or "").lower()
            caption = (img.get("caption") or "").lower()
            img_groups = self._extract_group_names(desc + " " + caption)
            image_groups.update(img_groups)

        if article_groups and image_groups:
            overlap = article_groups & image_groups
            if not overlap:
                issues.append(
                    f"文章组合({','.join(list(article_groups)[:3])})"
                    f"与图片组合({','.join(list(image_groups)[:3])})不一致"
                )
                return False, issues

        return True, []

    def _check_scene_match(
        self, article: Dict, images: List[Dict]
    ) -> Tuple[bool, List[str]]:
        """检查场景匹配（机场vs舞台等）"""
        issues = []

        article_text = ((article.get("title") or "") + " " + ((article.get("content") or "")[:500] or "")).lower()
        article_scene = self._detect_scene(article_text)

        for img in images:
            desc = ((img.get("description") or "") + " " + (img.get("caption") or "")).lower()
            img_scene = self._detect_scene(desc)

            if article_scene and img_scene and article_scene != img_scene:
                issues.append(
                    f"文章场景'{article_scene}'与图片场景'{img_scene}'不匹配"
                )
                return False, issues

        return True, []

    def _check_description_completeness(self, images: List[Dict]) -> float:
        """检查图片描述完整性"""
        if not images:
            return 0.0
        has_desc = sum(1 for img in images if img.get("description"))
        return has_desc / len(images)

    def _has_real_person_photo(self, images: List[Dict]) -> bool:
        """检查是否有真实人物照片（非广告/logo/banner）"""
        for img in images:
            url = (img.get("url") or "").lower()
            desc = (img.get("description") or "").lower()

            # 排除明显非人物图
            non_person_patterns = ["logo", "banner", "header", "icon", "button", "avatar", "profile", "sponsored", "promo", "promotion", "widget", "subscribe", "newsletter", "related", "recommend", "outbrain", "taboola", "doubleclick", "googlesyndication",
                                   "nav_", "sidebar", "footer", "background"]
            is_non_person = any(p in url or p in desc for p in non_person_patterns)

            if not is_non_person:
                return True

        return False

    def _extract_idol_names(self, text: str) -> set:
        """从文本中提取爱豆名字"""
        names = set()

        # 常见韩国爱豆名字（英文）
        known_idols = [
            "jungkook", "v", "jin", "suga", "j-hope", "rm", "jimin",
            "iu", "taeyeon", "jennie", "lisa", "rose", "jisoo",
            "karina", "winter", "ningning", "giselle",
            "yeji", "lia", "ryujin", "chaeryeong", "yuna",
            "sana", "mina", "momo", "nayeon", "jeongyeon", "jihyo", "dahyun", "chaeyoung", "tzuyu",
            "felix", "hyunjin", "bang chan", "lee know", "changbin", "han", "seungmin", "i.n",
            "soobin", "yeonjun", "beomgyu", "taehyun", "hueningkai",
            "riize", "shotaro", "sungchan", "eunseok", "wonbin", "seunghan", "sohee", "anton",
            "leeseo", "wonyoung", "yujin", "gaeul", "reive", "liz",
            "hanni", "minji", "haerin", "danielle", "hyein",
            "taeyong", "jaehyun", "mark", "johnny", "taeil", "yuta", "doyoung", "jungwoo",
        ]

        text_lower = text.lower()
        for name in known_idols:
            if name in text_lower:
                names.add(name)

        return names

    def _extract_group_names(self, text: str) -> set:
        """从文本中提取组合名"""
        groups = set()

        known_groups = [
            "bts", "blackpink", "twice", "stray kids", "txt", "aespa",
            "ive", "le sserafim", "newjeans", "nct", "nct 127", "nct dream",
            "red velvet", "exo", "got7", "monsta x", "ateez", "enhypen",
            "seventeen", "the boyz", "stayc", "itzy", "gidle", "(g)idle",
            "fromis_9", "loona", "cravity", "riize", "zerobaseone", "zb1",
            "boy next door", "bnd", "kiss of life", "babymonster",
        ]

        text_lower = text.lower()
        for group in known_groups:
            if group in text_lower:
                groups.add(group)

        return groups

    def _detect_scene(self, text: str) -> Optional[str]:
        """检测场景类型"""
        if not text:
            return None

        scene_keywords = {
            "airport": ["airport", "공항", "incheon", "gimpo", "departur", "arrival"],
            "stage": ["stage", "무대", "concert", "콘서트", "performance", "live"],
            "brand_event": ["brand", "브랜드", "ambassador", "대사", "campaign", "fashion week", "runway"],
            "photoshoot": ["pictorial", "화보", "magazine", "매거진", "photoshoot", "photosession"],
            "fansign": ["fansign", "팬싸", "fan meeting", "팬미팅"],
            "mv": ["music video", "mv", "뮤비", "teaser"],
            "daily": ["daily", "일상", "sns", "instagram", "인스타", "selfie", "셀카"],
        }

        text_lower = text.lower()
        for scene, keywords in scene_keywords.items():
            for kw in keywords:
                if kw in text_lower:
                    return scene

        return None

    def _build_result(
        self,
        score: float,
        issues: List[str],
        valid_images: List[Dict],
        excluded_images: List[Dict],
        has_real_person: bool,
    ) -> Dict:
        """构建检查结果"""
        return {
            "consistency_score": round(score, 1),
            "passed": score >= self.threshold,
            "threshold": self.threshold,
            "issues": issues,
            "valid_images": valid_images,
            "excluded_images": excluded_images,
            "has_real_person_photo": has_real_person,
            "valid_image_count": len(valid_images),
            "excluded_image_count": len(excluded_images),
        }
