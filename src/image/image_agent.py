"""③ 配图 Agent - 从 Tavily 源文章配图中下载（确保图文绑定）"""

import asyncio
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import httpx

from src.base_agent import BaseAgent, AgentStatus, AgentResult
from src.logger import logger

# 不能作为封面图或正文图的关键词（出现在 URL 或描述中即排除）
# 注意：只用完整单词/路径匹配，避免误判正常URL（如 /2026/06/ 不含 "ads/"）
BLOCKED_KEYWORDS = [
    "audition", "apply", "recruit", "trainee", "casting",
    "banner", "logo", "advertisement", "sponsor", "sponsored", "promo", "promotion", "widget", "icon", "avatar", "profile", "subscribe", "newsletter", "related", "recommend", "outbrain", "taboola", "doubleclick", "googlesyndication", "tracking", "affiliate", "campaign", "popup", "ads", "adserver", "googleads",
    "button", "icon", "favicon", "xwhite30.png", "placeholder",
    "sprite", "blank",
]


def _is_blocked_image(url: str, description: str = "") -> bool:
    """检查图片 URL 和描述是否包含被禁关键词"""
    url = url or ""
    description = description or ""
    text = (url + " " + description).lower()
    for kw in BLOCKED_KEYWORDS:
        if kw in text:
            return True
    return False


def _is_broken_starnews_image_url(url: str) -> bool:
    """Skip StarNews article-path image URLs that are known 404 candidates."""
    url_l = (url or "").lower()
    if "starnewskorea.com" not in url_l:
        return False
    if "image.starnewskorea.com/cdn-cgi/image/" in url_l:
        return False
    parsed = urlparse(url_l)
    path = parsed.path or ""
    return "/music/" in path and "/w=1200/" in path


def _is_logo_or_symbol(img_path: str) -> bool:
    """
    通过图片内容分析判断是否为 LOGO/符号/图标。
    
    检测维度：
    1. 颜色复杂度 — LOGO 通常颜色种类少（< 15 种主色）
    2. 边缘密度 — LOGO 边缘稀疏，照片边缘密集
    3. 白色/纯色背景比例 — LOGO 常有大面积纯色背景
    4. 宽高比 — LOGO 常为正方形或极端宽幅
    
    Returns:
        True = 是 LOGO/符号，应跳过
        False = 是正常照片
    """
    try:
        from pathlib import Path
        from PIL import Image as PILImage
        import numpy as np

        img = PILImage.open(img_path)
        if img.mode != "RGB":
            img = img.convert("RGB")

        arr = np.array(img)
        h, w = arr.shape[:2]

        # 维度1: 颜色复杂度 — 量化到较少颜色后统计唯一颜色数
        # 将每个通道量化到 4 级 (0-3)，共 4^3=64 种可能颜色
        quantized = (arr // 64).reshape(-1, 3)
        unique_colors = len(np.unique(quantized, axis=0))
        
        # 照片通常有 30+ 种量化颜色，LOGO 通常 < 15
        # 放宽阈值：< 8 才判定为 LOGO（偶像写真照可能颜色较少）
        if unique_colors < 8:
            logger.info(
                f"[配图Agent] 🚫 疑似LOGO(颜色过少): {unique_colors} 种量化颜色 | {Path(img_path).name}"
            )
            return True

        # 维度2: 边缘密度 — 用简单的梯度检测
        gray = np.mean(arr, axis=2).astype(np.uint8)
        # 计算水平梯度
        grad_x = np.abs(np.diff(gray, axis=1))
        # 计算垂直梯度
        grad_y = np.abs(np.diff(gray, axis=0))
        
        # 边缘像素比例（梯度 > 30 的像素占比）
        edge_ratio = (np.mean(grad_x > 30) + np.mean(grad_y > 30)) / 2
        
        # 照片边缘密度通常 > 0.05，LOGO 通常 < 0.03
        # 放宽：边缘比例 < 0.01 且颜色很少才判定为 LOGO
        if edge_ratio < 0.01 and unique_colors < 15:
            logger.info(
                f"[配图Agent] 🚫 疑似LOGO(边缘稀疏): 边缘比例={edge_ratio:.4f}, 颜色={unique_colors} | {Path(img_path).name}"
            )
            return True

        # 维度3: 纯色背景比例 — 检测最常见的颜色占比
        # 量化到 8 级后找最常见颜色
        quantized_8 = (arr // 32).reshape(-1, 3)
        colors, counts = np.unique(quantized_8, axis=0, return_counts=True)
        top_color_ratio = counts[0] / len(quantized_8)
        
        # 如果最常见颜色占比 > 75%，且颜色种类少，很可能是 LOGO/图标
        # 放宽：> 85% 才判定为 LOGO（偶像写真照可能有大面积纯色背景）
        if top_color_ratio > 0.85 and unique_colors < 20:
            logger.info(
                f"[配图Agent] 🚫 疑似LOGO(纯色背景过多): 主色占比={top_color_ratio:.2%}, 颜色={unique_colors} | {Path(img_path).name}"
            )
            return True

        # 维度4: 正方形且颜色少 — 典型 LOGO 特征
        # 放宽：颜色 < 12 才判定（偶像写真照可能是正方形构图）
        aspect = w / h
        if 0.8 < aspect < 1.2 and unique_colors < 12:
            logger.info(
                f"[配图Agent] 🚫 疑似LOGO(正方形+少颜色): {w}x{h}, 颜色={unique_colors} | {Path(img_path).name}"
            )
            return True

        logger.debug(
            f"[配图Agent] ✅ 图片内容验证通过: 颜色={unique_colors}, 边缘={edge_ratio:.4f}, "
            f"主色占比={top_color_ratio:.2%}, 尺寸={w}x{h} | {Path(img_path).name}"
        )
        return False

    except Exception as e:
        logger.warning(f"[配图Agent] 图片内容分析失败，保守保留: {e}")
        return False


def _assess_image_quality(
    img_path: str, thresholds: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """基于尺寸、文件大小、清晰度和信息量判断新闻图片视觉可用性。"""
    try:
        from PIL import Image as PILImage
        import numpy as np

        path = Path(img_path)
        image = PILImage.open(path).convert("RGB")
        width, height = image.size
        file_size = path.stat().st_size
        gray = np.asarray(image.convert("L").resize((min(width, 800), min(height, 800))))
        gray = gray.astype(np.float32)

        # 离散 Laplacian 方差：越低通常越模糊/虚化。
        laplacian = (
            -4 * gray
            + np.roll(gray, 1, axis=0)
            + np.roll(gray, -1, axis=0)
            + np.roll(gray, 1, axis=1)
            + np.roll(gray, -1, axis=1)
        )
        blur_score = float(np.var(laplacian[1:-1, 1:-1]))

        histogram = np.bincount(gray.astype(np.uint8).ravel(), minlength=256)
        probabilities = histogram[histogram > 0] / histogram.sum()
        entropy = float(-(probabilities * np.log2(probabilities)).sum())
        contrast = float(np.std(gray))

        thresholds = thresholds or {}
        min_width = thresholds.get("min_width", 480)
        min_height = thresholds.get("min_height", 320)
        min_file_size = thresholds.get("min_file_size_kb", 25) * 1024
        min_blur_score = thresholds.get("min_blur_score", 45)

        reasons = []
        if width < min_width or height < min_height:
            reasons.append(f"尺寸过小({width}x{height})")
        if file_size < min_file_size:
            reasons.append(f"文件过小({file_size // 1024}KB)")
        if blur_score < min_blur_score:
            reasons.append(f"清晰度不足(blur={blur_score:.1f})")
        if entropy < 3.2 or contrast < 18:
            reasons.append(
                f"信息量过低(entropy={entropy:.1f},contrast={contrast:.1f})"
            )

        score = 100
        score -= 35 if blur_score < min_blur_score else 0
        score -= 25 if width < min_width or height < min_height else 0
        score -= 20 if file_size < min_file_size else 0
        score -= 25 if entropy < 3.2 or contrast < 18 else 0
        return {
            "passed": not reasons,
            "quality_score": max(0, score),
            "blur_score": round(blur_score, 1),
            "entropy": round(entropy, 2),
            "contrast": round(contrast, 1),
            "width": width,
            "height": height,
            "file_size": file_size,
            "reason": "；".join(reasons),
        }
    except Exception as exc:
        return {
            "passed": False,
            "quality_score": 0,
            "reason": f"质量检测异常: {exc}",
        }


class ImageAgent(BaseAgent):
    """
    配图 Agent 职责：
    1. 接收写作 Agent 传递的 tavily_images（源文章配图，与内容绑定）
    2. 下载这些图片到本地
    3. 按封面图 / 文中插图 / 结尾图分类
    4. 图片优化处理（格式转换、压缩）
    5. 输出图片本地路径列表，供排版 Agent 使用

    核心原则：图片来自 Tavily 搜索的源文章，与文章内容天然绑定。
    不再使用 Unsplash/Pexels 等无关图库。
    """

    name = "image_agent"
    display_name = "③ 配图 Agent (Tavily 源图下载)"

    def __init__(self, config: Optional[Dict] = None):
        super().__init__(config)
        project_root = self.config.get("project_root", ".")
        self.image_dir = Path(project_root) / "data" / "images"
        self.image_dir.mkdir(parents=True, exist_ok=True)
        self.http_client = None

    async def _get_http(self):
        if self.http_client is None:
            self.http_client = httpx.AsyncClient(
                timeout=30.0,
                follow_redirects=True,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
            )
        return self.http_client

    async def execute(self, context: Dict) -> AgentResult:
        """执行配图流程"""
        # 从 context 中获取 Tavily 源文章配图
        tavily_images = list(context.get("tavily_images", []))
        topic_info = context.get("topic_info", {})
        article_url = topic_info.get("url", "") if isinstance(topic_info, dict) else ""
        if article_url:
            og_image = await self._fetch_og_image(article_url)
            if og_image and not any(
                (img.get("url") if isinstance(img, dict) else img) == og_image
                for img in tavily_images
            ):
                tavily_images.insert(0, {
                    "url": og_image,
                    "source": article_url,
                    "description": "article og:image",
                })
                logger.info(f"[配图Agent] 优先加入文章 og:image: {og_image[:80]}")

        if not tavily_images:
            logger.warning("[配图Agent] 写作Agent未传递 tavily_images，无图可用")
            return AgentResult(
                status=AgentStatus.FAILED,
                agent_name=self.name,
                error="无源文章配图可用，图片不合格，终止本篇文章",
            )

        logger.info(
            f"[配图Agent] 收到 {len(tavily_images)} 张 Tavily 源文章配图，开始筛选..."
        )

        # Step 1: 过滤 LOGO/广告/选秀等无效图片
        filtered_images = []
        for img_info in tavily_images:
            url = img_info.get("url", "") if isinstance(img_info, dict) else (img_info or "")
            desc = img_info.get("description", "") if isinstance(img_info, dict) else ""
            if not url:
                continue
            if _is_broken_starnews_image_url(url):
                logger.info(f"[配图Agent] 跳过StarNews无效拼接图片URL: {url[:100]}")
                continue
            if _is_blocked_image(url, desc):
                logger.info(f"[配图Agent] 跳过LOGO/广告图片: {url[:80]}")
                continue
            filtered_images.append(img_info)

        logger.info(
            f"[配图Agent] 关键词过滤: {len(tavily_images)} → {len(filtered_images)} 张"
        )

        if not filtered_images:
            filtered_images = await self._tavily_image_fallback(context)
            if not filtered_images:
                return AgentResult(
                    status=AgentStatus.FAILED,
                    agent_name=self.name,
                    error="所有图片被过滤且图片搜索预算不足或无结果",
                )

        # Step 2: 限制每篇文章最多3张图片
        max_images = 4
        if len(filtered_images) > max_images:
            logger.info(
                f"[配图Agent] 图片数量超过限制，截取前 {max_images} 张（原 {len(filtered_images)} 张）"
            )
            filtered_images = filtered_images[:max_images]

        # Step 3: 下载所有图片
        downloaded = []
        tasks = []

        for idx, img_info in enumerate(filtered_images):
            url = img_info.get("url", "") if isinstance(img_info, dict) else img_info
            if not url:
                continue
            tasks.append(self._download_image(url, img_info, idx))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.warning(f"[配图Agent] 图片{i} 下载失败: {result}")
            elif isinstance(result, dict) and result.get("path"):
                downloaded.append(result)
                logger.info(f"[配图Agent] ✅ 下载: {result['filename']} ← {result['source_url'][:80]}")

        if not downloaded:
            fallback_images = await self._tavily_image_fallback(context)
            if fallback_images:
                fallback_results = await asyncio.gather(*[
                    self._download_image(
                        img.get("url", ""),
                        img,
                        len(filtered_images) + idx,
                    )
                    for idx, img in enumerate(fallback_images)
                    if img.get("url")
                ], return_exceptions=True)
                downloaded = [
                    result for result in fallback_results
                    if isinstance(result, dict) and result.get("path")
                ]
            if not downloaded:
                return AgentResult(
                    status=AgentStatus.FAILED,
                    agent_name=self.name,
                    error="文章图片、og:image、Tavily备用图片均失败",
                )

        # Step 4: LOGO/符号图片内容检测（基于颜色复杂度、边缘密度等）
        valid_downloaded = []
        quality_rejected = []
        for img in downloaded:
            if _is_logo_or_symbol(img["path"]):
                logger.info(f"[配图Agent] 🚫 跳过LOGO/符号图片: {img['filename']}")
                # 删除无效图片文件
                try:
                    Path(img["path"]).unlink(missing_ok=True)
                except Exception:
                    pass
                quality_rejected.append({**img, "quality_reason": "疑似LOGO/符号"})
                continue

            quality = _assess_image_quality(
                img["path"],
                self.get_config("topic_agent.image", {}),
            )
            relevance = self._assess_image_relevance(topic_info, img)
            img.update({
                "quality_passed": quality["passed"],
                "quality_score": quality.get("quality_score", 0),
                "blur_score": quality.get("blur_score", 0),
                "quality_reason": quality.get("reason", ""),
                "relevance_passed": relevance["passed"],
                "relevance_reason": relevance.get("reason", ""),
            })
            if not quality["passed"] or not relevance["passed"]:
                quality_rejected.append(img)
                logger.info(
                    f"[配图Agent] 🚫 视觉质量不合格: {img['filename']} | "
                    f"{quality['reason'] or relevance.get('reason', '')}"
                )
                try:
                    Path(img["path"]).unlink(missing_ok=True)
                except Exception:
                    pass
                continue
            valid_downloaded.append(img)

        logger.info(
            f"[配图Agent] 内容检测: {len(downloaded)} → {len(valid_downloaded)} 张 "
            f"(排除 {len(downloaded) - len(valid_downloaded)} 张低质/无效图)"
        )

        if not valid_downloaded:
            return AgentResult(
                status=AgentStatus.FAILED,
                agent_name=self.name,
                error="所有下载图片均未通过视觉质量检查",
            )

        if len(quality_rejected) > len(downloaded) / 2:
            logger.warning(
                f"[配图Agent] ⚠️ 多张图片不可用({len(quality_rejected)}/{len(downloaded)})，"
                "但保留已通过图片继续"
            )

        quality_warning = None
        cover_only_mode = (
            len(valid_downloaded) == 1
            and bool(self.get_config("topic_agent.image.allow_cover_only_mode", True))
        )
        if cover_only_mode:
            quality_warning = "仅1张有效图片，进入封面图模式"
            logger.warning("[配图Agent] ⚠️ 仅1张有效图片，进入封面图模式")
            logger.info("[配图Agent] 正文图为空，继续排版")

        # 质量最高且最清晰的图片优先作为封面。
        if self.get_config("topic_agent.image.cover_face_priority", True):
            for img in valid_downloaded:
                img["cover_face_score"] = self._cover_face_score(img.get("path", ""))
        valid_downloaded.sort(
            key=lambda item: (
                item.get("cover_face_score", 0),
                item.get("quality_score", 0),
                item.get("blur_score", 0),
                item.get("file_size", 0),
            ),
            reverse=True,
        )

        # Step 5: 分类（第一张作为封面，其余作为文中插图）
        categorized = self._categorize_images(valid_downloaded)

        # Step 5: 为封面图生成智能裁剪版本（确保人物头部完整）
        if categorized["cover"]:
            cover = categorized["cover"][0]
            smart_path = self._create_smart_cover(cover["path"])
            if smart_path:
                cover["original_path"] = cover["path"]
                cover["path"] = smart_path
                cover["filename"] = Path(smart_path).name
                logger.info(f"[配图Agent] ✅ 封面图智能裁剪: {cover['filename']}")

        logger.info(
            f"[配图Agent] ✅ 下载完成: 共 {len(valid_downloaded)} 张 | "
            f"封面: {len(categorized['cover'])} | 文中: {len(categorized['inline'])} | "
            f"结尾: {len(categorized['footer'])}"
        )

        all_images = categorized["cover"] + categorized["inline"] + categorized["footer"]

        return AgentResult(
            status=AgentStatus.SUCCESS,
            agent_name=self.name,
            output={
                "images": all_images,
                "cover_image": categorized["cover"][0] if categorized["cover"] else None,
                "inline_images": categorized["inline"],
                "footer_images": categorized["footer"],
                "total_count": len(all_images),
                "cover_only_mode": cover_only_mode,
                "image_dir": str(self.image_dir),
                "quality_warning": quality_warning,
                "rejected_image_count": len(quality_rejected),
            },
        )

    @staticmethod
    def _assess_image_relevance(topic_info: Dict, image: Dict) -> Dict[str, Any]:
        """有明确人物元数据时，拒绝与文章艺人明显不一致的图片。"""
        try:
            from src.topic.scoring import ScoringSystem

            article_text = " ".join([
                str(topic_info.get("title", "")),
                str(topic_info.get("summary", "")),
                str(topic_info.get("url", "")),
            ])
            image_text = " ".join([
                str(image.get("description", "")),
                str(image.get("article_title", "")),
                str(image.get("article_source", "")),
                str(image.get("source_url", "")),
            ])
            article_hits = {
                star for star in ScoringSystem.TOP_STARS
                if ScoringSystem._match_star(star, article_text)
            }
            image_hits = {
                star for star in ScoringSystem.TOP_STARS
                if ScoringSystem._match_star(star, image_text)
            }
            if article_hits and image_hits and not article_hits.intersection(image_hits):
                return {
                    "passed": False,
                    "reason": (
                        f"图片人物与文章不一致: "
                        f"{sorted(article_hits)[:2]} vs {sorted(image_hits)[:2]}"
                    ),
                }
            return {"passed": True, "reason": ""}
        except Exception as exc:
            logger.warning(f"[配图Agent] 图片相关性检测异常，保守继续: {exc}")
            return {"passed": True, "reason": ""}

    async def _download_image(self, url: str, img_info: Dict, idx: int) -> Optional[Dict]:
        """下载单张图片到本地（统一转换为标准JPEG，确保微信兼容）"""
        http = await self._get_http()

        # 跳过 SVG 图片（微信不支持，且通常是logo/icon）
        if ".svg" in url.lower():
            logger.info(f"[配图Agent] 跳过SVG图片: {url[:80]}")
            return None

        try:
            response = await http.get(url, headers=self._headers_for_url(url))
            response.raise_for_status()

            # 跳过 SVG（通过 content-type 二次检查）
            content_type = response.headers.get("content-type", "")
            if "svg" in content_type:
                logger.info(f"[配图Agent] 跳过SVG图片(content-type): {url[:80]}")
                return None

            # 统一使用 PIL 验证并转换为标准 JPEG
            # 解决问题：WEBP伪装成JPG、CMYK色彩空间、损坏文件、渐进式JPEG等
            from io import BytesIO
            from PIL import Image as PILImage

            try:
                img = PILImage.open(BytesIO(response.content))
                img.load()  # 强制加载像素数据，检测损坏文件
            except Exception as e:
                logger.warning(f"[配图Agent] 图片解析失败，跳过: {url[:80]} | {e}")
                return None

            # 过滤过小的图片（< 100x100 通常是 icon/placeholder/logo）
            min_width, min_height = 100, 100
            if img.size[0] < min_width or img.size[1] < min_height:
                logger.info(
                    f"[配图Agent] 跳过过小图片: {img.size[0]}x{img.size[1]} < {min_width}x{min_height} | {url[:80]}"
                )
                return None

            # 转换为 RGB（微信不支持 CMYK/RGBA/P 等模式）
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            elif img.mode == "L":
                # 灰度图也转为 RGB，确保兼容
                img = img.convert("RGB")

            # 统一保存为标准 JPEG（baseline, quality=90）
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"tavily_{timestamp}_{idx:03d}.jpg"
            filepath = self.image_dir / filename
            img.save(filepath, "JPEG", quality=90, optimize=True)

            file_size = filepath.stat().st_size
            # 过滤广告横幅 / 极端比例图：这类图即使有人脸，也经常是广告位、推荐位、跳转卡片
            w, h = img.size
            ratio = w / max(h, 1)
            if ratio > 3.2 or ratio < 0.32:
                logger.info(f"[配图Agent] 🚫 跳过极端比例疑似广告/装饰图: {w}x{h}, ratio={ratio:.2f} | {url[:80]}")
                return None

            logger.info(f"[配图Agent] ✅ 图片转换成功: {filename} ({img.size[0]}x{img.size[1]}, {file_size//1024}KB)")

            # 获取图片信息
            description = ""
            source_url = ""
            source_title = ""
            if isinstance(img_info, dict):
                description = img_info.get("description", "")
                source_url = img_info.get("source", "")
                source_title = img_info.get("source_title", "")

            return {
                "path": str(filepath),
                "filename": filename,
                "source_url": url,
                "description": description,
                "article_source": source_url,
                "article_title": source_title,
                "file_size": file_size,
                "content_type": "image/jpeg",
                "width": img.size[0],
                "height": img.size[1],
            }

        except Exception as e:
            logger.error(f"[配图Agent] 下载失败 {url[:80]}: {e}")
            return None

    @staticmethod
    def _headers_for_url(url: str) -> Dict[str, str]:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/126.0.0.0 Safari/537.36"
            )
        }
        host = urlparse(url).netloc.lower()
        if "allkpop.com" in host:
            headers["Referer"] = "https://www.allkpop.com/"
        elif "koreaboo.com" in host:
            headers["Referer"] = "https://www.koreaboo.com/"
        elif "soompi.com" in host or "soompi.io" in host:
            headers["Referer"] = "https://www.soompi.com/"
        return headers

    async def _fetch_og_image(self, article_url: str) -> Optional[str]:
        """读取文章 og:image，失败时静默回退到 Tavily 图片。"""
        try:
            http = await self._get_http()
            response = await http.get(
                article_url,
                headers=self._headers_for_url(article_url),
            )
            response.raise_for_status()
            html = response.text[:500_000]
            patterns = [
                r'<meta[^>]+property=["\']og:image(?::secure_url)?["\'][^>]+content=["\']([^"\']+)',
                r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image(?::secure_url)?["\']',
            ]
            for pattern in patterns:
                match = re.search(pattern, html, flags=re.IGNORECASE)
                if match and not _is_blocked_image(match.group(1)):
                    return match.group(1)
        except Exception as exc:
            logger.info(f"[配图Agent] og:image 获取失败，使用 Tavily 图片: {exc}")
        return None

    async def _tavily_image_fallback(self, context: Dict) -> List[Dict]:
        """仅在已有图片失败后执行，且与选题搜索共享 credits 账本。"""
        budget = context.get("tavily_budget")
        if not isinstance(budget, dict):
            logger.warning("[配图Agent] 缺少共享 Tavily 预算，跳过备用搜图")
            return []
        max_image_calls = min(
            budget.get("max_image_calls", 2),
            self.get_config("topic_agent.image.max_image_search_queries", 2),
        )
        if budget.get("image_calls", 0) >= max_image_calls:
            logger.warning("[配图Agent] Tavily 图片搜索次数预算已耗尽")
            return []

        cost = 1  # 图片 fallback 强制 basic
        projected = budget.get("credits", 0) + cost
        absolute_max = budget.get("absolute_max_credits", 50)
        hard_stop = budget.get("hard_stop_credits", 20)
        default_max = budget.get("max_total_credits", 12)
        if projected > absolute_max:
            budget["hard_stop_triggered"] = True
            logger.error("[配图Agent] Tavily absolute max 触发，强制停止备用搜图")
            return []
        if projected > hard_stop:
            budget["hard_stop_triggered"] = True
            logger.error("[配图Agent] Tavily hard stop 触发，停止备用搜图")
            return []
        if projected > default_max:
            logger.warning("[配图Agent] Tavily 默认credits预算不足，切换下一篇")
            return []

        topic = context.get("topic_info", {})
        title = topic.get("title", "") if isinstance(topic, dict) else ""
        if not title:
            return []
        budget["credits"] = projected
        budget["calls"] = budget.get("calls", 0) + 1
        budget["image_calls"] = budget.get("image_calls", 0) + 1

        try:
            http = await self._get_http()
            response = await http.post(
                f"{self.get_config('tavily.base_url', 'https://api.tavily.com')}/search",
                json={
                    "query": f"{title} official press photo",
                    "search_depth": "basic",
                    "auto_parameters": False,
                    "include_answer": False,
                    "include_raw_content": False,
                    "include_images": True,
                    "max_results": 1,
                    "topic": "news",
                },
                headers={
                    "Authorization": f"Bearer {self.get_config('tavily.api_key', '')}",
                    "Content-Type": "application/json",
                },
            )
            response.raise_for_status()
            data = response.json()
            images = list(data.get("images", []))
            for result in data.get("results", []):
                images.extend(result.get("images", []))
            normalized = []
            for image in images:
                item = image if isinstance(image, dict) else {"url": image}
                if item.get("url") and not _is_blocked_image(
                    item["url"], item.get("description", "")
                ):
                    normalized.append(item)
            logger.info(
                f"[配图Agent] Tavily备用搜图调用 "
                f"{budget['image_calls']}/{max_image_calls} | "
                f"累计credits={budget['credits']} | 结果={len(normalized)}"
            )
            return normalized[:3]
        except Exception as exc:
            logger.warning(f"[配图Agent] Tavily备用搜图失败: {exc}")
            return []

    def _categorize_images(self, downloaded: List[Dict]) -> Dict[str, List[Dict]]:
        """
        将下载的图片分类：
        - 封面图：取第一张（通常相关性最高）
        - 文中插图：中间的图片
        - 结尾图：最后一张
        """
        if not downloaded:
            return {"cover": [], "inline": [], "footer": []}

        if len(downloaded) == 1:
            # 只有一张图，用作封面
            downloaded[0]["position"] = "cover"
            return {"cover": [downloaded[0]], "inline": [], "footer": []}

        if len(downloaded) == 2:
            # 两张图：封面 + 文中
            cover = [downloaded[0]]
            inline = [downloaded[1]]
            for img in cover:
                img["position"] = "cover"
            for img in inline:
                img["position"] = "inline"
            return {"cover": cover, "inline": inline, "footer": []}

        # 三张以上：封面 + 文中 + 结尾
        cover = [downloaded[0]]
        footer = [downloaded[-1]]
        inline = downloaded[1:-1]

        # 标注位置
        for img in cover:
            img["position"] = "cover"
        for img in inline:
            img["position"] = "inline"
        for img in footer:
            img["position"] = "footer"

        return {"cover": cover, "inline": inline, "footer": footer}

    @staticmethod
    def _cover_face_score(image_path: str) -> int:
        """Return a simple cover priority score for images with visible faces."""
        if not image_path:
            return 0
        try:
            import cv2
            from PIL import Image as PILImage
            import numpy as np

            img = PILImage.open(image_path)
            if img.mode != "RGB":
                img = img.convert("RGB")
            arr = np.array(img)
            gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
            cascade_path = (
                cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
            )
            face_cascade = cv2.CascadeClassifier(cascade_path)
            faces = face_cascade.detectMultiScale(
                gray, scaleFactor=1.1, minNeighbors=3, minSize=(30, 30)
            )
            if len(faces) == 0:
                return 0
            height, width = gray.shape[:2]
            largest = max(faces, key=lambda r: r[2] * r[3])
            _, fy, fw, fh = largest
            face_area_ratio = (fw * fh) / max(width * height, 1)
            upper_bonus = 20 if fy < height * 0.55 else 0
            return int(60 + min(face_area_ratio * 500, 30) + upper_bonus)
        except Exception:
            return 0

    def _create_safe_cover_canvas(self, img, target_ratio: float):
        """Create a cover canvas that preserves the full subject instead of a body-only crop."""
        try:
            from PIL import ImageFilter

            width, height = img.size
            canvas_w = width
            canvas_h = max(1, int(canvas_w / target_ratio))
            if canvas_h > height:
                canvas_h = height
                canvas_w = max(1, int(canvas_h * target_ratio))

            bg = img.copy()
            bg_ratio = bg.size[0] / bg.size[1]
            if bg_ratio < target_ratio:
                bg_w = canvas_w
                bg_h = int(bg_w / bg_ratio)
            else:
                bg_h = canvas_h
                bg_w = int(bg_h * bg_ratio)
            bg = bg.resize((bg_w, bg_h))
            left = max(0, (bg_w - canvas_w) // 2)
            top = max(0, int((bg_h - canvas_h) * 0.30))
            bg = bg.crop((left, top, left + canvas_w, top + canvas_h))
            bg = bg.filter(ImageFilter.GaussianBlur(radius=18))

            fg = img.copy()
            fg_h = canvas_h
            fg_w = max(1, int(fg_h * width / height))
            if fg_w > canvas_w:
                fg_w = canvas_w
                fg_h = max(1, int(fg_w * height / width))
            fg = fg.resize((fg_w, fg_h))
            x = (canvas_w - fg_w) // 2
            y = max(0, (canvas_h - fg_h) // 2)
            bg.paste(fg, (x, y))
            return bg
        except Exception:
            return None

    def _create_smart_cover(self, image_path: str) -> Optional[str]:
        """
        为封面图创建智能裁剪版本，确保人物头部完整。

        微信公众号封面图比例为 2.35:1（约 900x383px）。
        对于竖版人物照片，直接裁剪可能切掉头部。

        策略：
        1. 使用 OpenCV 检测人脸位置
        2. 以人脸为中心进行裁剪
        3. 如果检测不到人脸，对竖版图取上方 40% 区域（头部通常在上方）
        4. 对横版图居中裁剪
        """
        try:
            import cv2
            from PIL import Image as PILImage
            import numpy as np

            # 读取图片
            img = PILImage.open(image_path)
            if img.mode != "RGB":
                img = img.convert("RGB")

            width, height = img.size

            # 微信封面比例 2.35:1
            target_ratio = 2.35

            # 如果图片本身就是接近目标比例，不需要裁剪
            current_ratio = width / height
            if abs(current_ratio - target_ratio) < 0.3:
                logger.debug(f"[配图Agent] 图片比例已接近封面比例({current_ratio:.2f})，无需裁剪")
                return None

            # 转换为 OpenCV 格式进行人脸检测
            cv_img = np.array(img)
            cv_img = cv2.cvtColor(cv_img, cv2.COLOR_RGB2BGR)

            # 使用 Haar Cascade 检测人脸
            cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
            face_cascade = cv2.CascadeClassifier(cascade_path)
            gray = cv2.cvtColor(cv_img, cv2.COLOR_BGR2GRAY)
            faces = face_cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=3, minSize=(30, 30))

            # 计算裁剪区域
            if current_ratio < target_ratio:
                # 竖版图片：需要从高度方向裁剪
                new_height = int(width / target_ratio)

                if len(faces) > 0:
                    # 有人脸：以第一张脸为中心裁剪
                    faces = sorted(faces, key=lambda r: r[2] * r[3], reverse=True)
                    fx, fy, fw, fh = faces[0]
                    face_center_y = fy + fh // 2
                    if (
                        self.get_config("topic_agent.image.avoid_body_only_cover", True)
                        and new_height < fh * 2.2
                    ):
                        cropped = self._create_safe_cover_canvas(img, target_ratio)
                        if cropped is None:
                            return None
                        start_y = 0
                    else:
                        # Put face in the upper third and keep headroom.
                        start_y = face_center_y - int(new_height * 0.35)
                        max_start_for_headroom = max(0, fy - int(new_height * 0.18))
                        start_y = min(start_y, max_start_for_headroom)
                        # Ensure face bottom remains inside the crop.
                        start_y = min(start_y, fy + fh - int(new_height * 0.68))
                        # 确保不超出底部
                        start_y = min(start_y, height - new_height)
                        start_y = max(0, start_y)
                        cropped = img.crop((0, start_y, width, start_y + new_height))
                    # 确保不超出底部
                    logger.info(
                        f"[配图Agent] 人脸检测成功: ({fx},{fy},{fw},{fh}), "
                        f"裁剪起始Y={start_y}, 高度={new_height}"
                    )
                else:
                    # 无人脸：对竖版图取上方区域（头部通常在上1/3）
                    # 取从 5% 到 5%+new_height 的区域
                    if self.get_config("topic_agent.image.avoid_body_only_cover", True):
                        cropped = self._create_safe_cover_canvas(img, target_ratio)
                        if cropped is None:
                            return None
                        start_y = 0
                    else:
                        start_y = int(height * 0.05)
                        if start_y + new_height > height:
                            start_y = height - new_height
                        start_y = max(0, start_y)
                        cropped = img.crop((0, start_y, width, start_y + new_height))
                    logger.info(
                        f"[配图Agent] 未检测到人脸, 竖版图取上方区域: start_y={start_y}, h={new_height}"
                    )

            else:
                # 横版图片：从宽度方向居中裁剪
                new_width = int(height * target_ratio)
                start_x = (width - new_width) // 2

                if len(faces) > 0:
                    # 有人脸：以人脸水平中心裁剪
                    faces = sorted(faces, key=lambda r: r[2] * r[3], reverse=True)
                    fx, fy, fw, fh = faces[0]
                    face_center_x = fx + fw // 2
                    start_x = face_center_x - new_width // 2
                    start_x = max(0, min(start_x, width - new_width))
                    logger.info(
                        f"[配图Agent] 人脸检测成功(横版): 裁剪起始X={start_x}, 宽度={new_width}"
                    )

                cropped = img.crop((start_x, 0, start_x + new_width, height))

            # 保存裁剪后的封面图
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            cover_filename = f"cover_{timestamp}.jpg"
            cover_path = str(self.image_dir / cover_filename)
            cropped.save(cover_path, "JPEG", quality=90, optimize=True)

            logger.info(
                f"[配图Agent] 封面裁剪: {width}x{height} → {cropped.size[0]}x{cropped.size[1]}"
            )
            return cover_path

        except ImportError:
            logger.warning("[配图Agent] cv2 未安装，跳过智能封面裁剪")
            return None
        except Exception as e:
            logger.warning(f"[配图Agent] 智能封面裁剪失败: {e}")
            return None

    async def cleanup(self):
        """清理 HTTP 客户端"""
        if self.http_client:
            await self.http_client.aclose()
