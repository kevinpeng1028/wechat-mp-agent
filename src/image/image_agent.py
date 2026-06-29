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
    "button", "icon", "favicon",
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
        tavily_images = context.get("tavily_images", [])

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
            if _is_blocked_image(url, desc):
                logger.info(f"[配图Agent] 跳过LOGO/广告图片: {url[:80]}")
                continue
            filtered_images.append(img_info)

        logger.info(
            f"[配图Agent] 关键词过滤: {len(tavily_images)} → {len(filtered_images)} 张"
        )

        if not filtered_images:
            return AgentResult(
                status=AgentStatus.FAILED,
                agent_name=self.name,
                error="所有图片被关键词过滤，图片不合格，终止本篇文章",
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
            return AgentResult(
                status=AgentStatus.FAILED,
                agent_name=self.name,
                error="所有图片下载失败，终止本篇文章",
            )

        # Step 4: LOGO/符号图片内容检测（基于颜色复杂度、边缘密度等）
        valid_downloaded = []
        for img in downloaded:
            if _is_logo_or_symbol(img["path"]):
                logger.info(f"[配图Agent] 🚫 跳过LOGO/符号图片: {img['filename']}")
                # 删除无效图片文件
                try:
                    Path(img["path"]).unlink(missing_ok=True)
                except Exception:
                    pass
            else:
                valid_downloaded.append(img)

        logger.info(
            f"[配图Agent] 内容检测: {len(downloaded)} → {len(valid_downloaded)} 张 "
            f"(排除 {len(downloaded) - len(valid_downloaded)} 张LOGO/符号)"
        )

        if not valid_downloaded:
            return AgentResult(
                status=AgentStatus.PARTIAL,
                agent_name=self.name,
                output={"images": [], "message": "所有图片被LOGO/符号检测过滤"},
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
                "image_dir": str(self.image_dir),
            },
        )

    async def _download_image(self, url: str, img_info: Dict, idx: int) -> Optional[Dict]:
        """下载单张图片到本地（统一转换为标准JPEG，确保微信兼容）"""
        http = await self._get_http()

        # 跳过 SVG 图片（微信不支持，且通常是logo/icon）
        if ".svg" in url.lower():
            logger.info(f"[配图Agent] 跳过SVG图片: {url[:80]}")
            return None

        try:
            response = await http.get(url)
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
            raise

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
                    # 确保脸部在裁剪区域内，且上方有空间
                    start_y = face_center_y - new_height // 2
                    # 确保头部上方有足够空间（至少 15% 的新高度）
                    min_start = max(0, fy - int(new_height * 0.15))
                    start_y = max(min_start, start_y)
                    # 确保不超出底部
                    start_y = min(start_y, height - new_height)
                    start_y = max(0, start_y)
                    logger.info(
                        f"[配图Agent] 人脸检测成功: ({fx},{fy},{fw},{fh}), "
                        f"裁剪起始Y={start_y}, 高度={new_height}"
                    )
                else:
                    # 无人脸：对竖版图取上方区域（头部通常在上1/3）
                    # 取从 5% 到 5%+new_height 的区域
                    start_y = int(height * 0.05)
                    if start_y + new_height > height:
                        start_y = height - new_height
                    start_y = max(0, start_y)
                    logger.info(
                        f"[配图Agent] 未检测到人脸, 竖版图取上方区域: start_y={start_y}, h={new_height}"
                    )

                # 裁剪
                cropped = img.crop((0, start_y, width, start_y + new_height))

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
