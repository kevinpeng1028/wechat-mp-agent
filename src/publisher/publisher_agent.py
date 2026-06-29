"""⑤ 发布 Agent - 封面/正文图上传 + 模板HTML渲染 + 草稿创建（头条+次条）"""

import asyncio
import base64
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx

from src.base_agent import BaseAgent, AgentStatus, AgentResult
from src.logger import logger


class PublisherAgent(BaseAgent):
    """
    发布 Agent 职责：
    1. 上传封面图到微信素材库 → 获取 thumb_media_id
    2. 上传正文图到微信素材库 → 获取微信CDN URL
    3. 使用排版后的HTML（含微信图片URL）
    4. 调用 draft/add 创建草稿（支持头条+次条多图文）
    5. 显示 draft media_id 和检查结果

    发布流程：
    - 头条文章：第一篇文章，使用其封面图
    - 次条文章：第二篇文章，使用其封面图
    - 多图文草稿：一次调用 draft/add 可包含多篇文章
    """

    name = "publisher_agent"
    display_name = "⑤ 发布 Agent (封面+正文图上传+草稿)"

    def __init__(self, config: Optional[Dict] = None):
        super().__init__(config)
        self.base_url = self.get_config("wechat_mp.base_url", "https://api.weixin.qq.com")
        self.app_id = self.get_config("wechat_mp.app_id", "")
        self.app_secret = self.get_config("wechat_mp.app_secret", "")
        self._access_token: Optional[str] = None
        self._token_expires_at: float = 0
        self.http_client = None

    async def _get_http(self) -> httpx.AsyncClient:
        if self.http_client is None:
            self.http_client = httpx.AsyncClient(timeout=60.0)
        return self.http_client

    async def execute(self, context: Dict) -> AgentResult:
        """
        执行发布流程

        context 包含:
        - formatted_articles: 排版后的文章列表（头条+次条）
        """
        formatted_articles = context.get("formatted_articles", [])
        if not formatted_articles:
            return AgentResult(
                status=AgentStatus.FAILED,
                agent_name=self.name,
                error="缺少 formatted_articles",
            )

        logger.info(f"[发布Agent] 开始处理 {len(formatted_articles)} 篇文章的发布...")

        # Step 1: 获取 access_token
        token = await self._get_access_token()
        if not token:
            return AgentResult(
                status=AgentStatus.FAILED,
                agent_name=self.name,
                error="无法获取微信 access_token",
            )

        # Step 2: 对每篇文章上传图片并准备草稿数据
        draft_articles = []

        for i, article in enumerate(formatted_articles):
            if not article.get("is_success"):
                logger.warning(f"[发布Agent] 文章 {i+1} 排版未成功，跳过")
                continue

            logger.info(
                f"[发布Agent] 处理文章 {i+1}: '{article.get('title','?')[:40]}'"
            )

            draft_article = await self._prepare_draft_article(article, token)
            if draft_article:
                draft_articles.append(draft_article)

        if not draft_articles:
            return AgentResult(
                status=AgentStatus.FAILED,
                agent_name=self.name,
                error="无成功处理的文章可用于草稿",
            )

        # Step 3: 创建多图文草稿
        draft_result = await self._create_multi_draft(token, draft_articles)

        if not draft_result.get("media_id"):
            return AgentResult(
                status=AgentStatus.FAILED,
                agent_name=self.name,
                error=f"草稿创建失败: {draft_result.get('error', '未知错误')}",
            )

        media_id = draft_result["media_id"]
        logger.info(f"[发布Agent] ✅ 草稿创建成功! media_id={media_id}")

        # 打印草稿信息
        self._print_draft_summary(draft_articles, media_id)

        # 保存发布记录
        await self._save_publish_record(media_id, draft_articles)

        return AgentResult(
            status=AgentStatus.SUCCESS,
            agent_name=self.name,
            output={
                "draft_media_id": media_id,
                "article_count": len(draft_articles),
                "articles": [
                    {
                        "title": a["title"],
                        "position": a.get("position", "unknown"),
                        "thumb_media_id": a.get("thumb_media_id", ""),
                    }
                    for a in draft_articles
                ],
                "publish_mode": "draft_only",
            },
        )

    async def _prepare_draft_article(self, article: Dict, token: str) -> Optional[Dict]:
        """准备单篇文章的草稿数据（上传图片）

        封面图优先级：
        1. 配图Agent生成的智能裁剪封面图（cover_image.path）
        2. 正文第一张有效图
        3. 模板Banner图（回退）
        """
        title = article.get("title", "")
        summary = article.get("summary", "")
        html = article.get("html_content", "")

        if not html:
            logger.error(f"[发布Agent] 文章 '{title[:40]}' 无HTML内容")
            return None

        # 上传封面图（优先使用智能裁剪的封面图）
        cover_image = article.get("cover_image")
        thumb_media_id = ""
        if cover_image:
            thumb_result = await self._upload_cover_image(cover_image, token)
            if thumb_result:
                thumb_media_id = thumb_result.get("media_id", "")
                logger.info(f"[发布Agent] 封面图上传成功: thumb_media_id={thumb_media_id}")
            else:
                logger.warning(f"[发布Agent] 封面图上传失败，尝试其他图片")

        # 上传正文图并替换HTML中的路径（同时移除上传失败的图片及说明）
        valid_images = article.get("valid_images", [])
        if valid_images:
            html = await self._upload_inline_images_and_replace(html, valid_images, token)

        # 如果没有封面图 media_id，尝试使用正文第一张图
        if not thumb_media_id and valid_images:
            for img in valid_images:
                # 跳过非封面位置的图片
                if img.get("position") == "footer":
                    continue
                thumb_result = await self._upload_cover_image(img, token)
                if thumb_result:
                    thumb_media_id = thumb_result.get("media_id", "")
                    logger.info(f"[发布Agent] 使用正文图作为封面: thumb_media_id={thumb_media_id}")
                    break

        # 如果仍然没有封面图，使用模板Banner图作为回退
        if not thumb_media_id:
            banner_url = self.get_config(
                "template_system.banner_img_url",
                "https://mmbiz.qpic.cn/mmbiz_jpg/17bia319bpNPA9cSuvY8QC8iapicHE8kHIlFwffSf0VV7wueSPPXP5EAfFvxM6bmJMicpfj59mCnkiaibTa09O376iaJJmWvOYoKV9KKpB1h2A8cjM/640?wx_fmt=jpeg"
            )
            logger.info(f"[发布Agent] 使用模板Banner图作为封面回退")
            temp_path = await self._download_temp_image(banner_url)
            if temp_path:
                thumb_result = await self._upload_cover_image({"path": temp_path}, token)
                if thumb_result:
                    thumb_media_id = thumb_result.get("media_id", "")
                    logger.info(f"[发布Agent] Banner封面图上传成功: thumb_media_id={thumb_media_id}")

        if not thumb_media_id:
            logger.error(f"[发布Agent] 文章 '{title[:40]}' 无法获取封面图，跳过")
            return None

        return {
            "title": title,
            "summary": summary,
            "content": html,
            "thumb_media_id": thumb_media_id,
            "position": article.get("position", "unknown"),
            "digest": summary[:64] if summary else title[:64],
        }

    async def _upload_cover_image(self, image_info: Dict, token: str) -> Optional[Dict]:
        """上传封面图（使用缩略图上传接口获取 thumb_media_id）"""
        img_path = image_info.get("path", "")
        if not img_path or not Path(img_path).exists():
            # 如果是URL，先下载
            url = image_info.get("url", "")
            if url:
                img_path = await self._download_temp_image(url)
            if not img_path:
                return None

        url = f"{self.base_url}/cgi-bin/material/add_material"
        params = {"access_token": token, "type": "image"}

        try:
            http = await self._get_http()
            with open(img_path, "rb") as f:
                files = {"media": (Path(img_path).name, f, "image/jpeg")}
                response = await http.post(url, params=params, files=files)
            data = response.json()

            if "media_id" in data:
                return {"media_id": data["media_id"], "url": data.get("url", "")}
            else:
                logger.error(f"[发布Agent] 封面图上传失败: {data}")
                return None

        except Exception as e:
            logger.error(f"[发布Agent] 封面图上传异常: {e}")
            return None

    async def _upload_inline_images_and_replace(
        self, html: str, images: List[Dict], token: str
    ) -> str:
        """上传正文图并替换HTML中的路径为微信CDN URL

        如果图片上传失败，从HTML中移除该 <img> 标签及其紧随的图片说明 <p> 标签。
        """
        # 建立本地路径/URL → 微信URL 的映射
        path_to_wx = {}
        # 记录上传失败的路径/URL（需要从HTML中移除）
        failed_paths = set()

        for img in images:
            # 优先使用本地路径
            local_path = img.get("path", "")
            url = img.get("url", "")

            if local_path and Path(local_path).exists():
                upload_result = await self._upload_inline_image(local_path, token)
            elif url:
                # 下载后上传
                temp_path = await self._download_temp_image(url)
                if temp_path:
                    upload_result = await self._upload_inline_image(temp_path, token)
                else:
                    upload_result = None
            else:
                continue

            if upload_result and upload_result.get("url"):
                wx_url = upload_result["url"]
                if local_path:
                    path_to_wx[local_path] = wx_url
                if url:
                    path_to_wx[url] = wx_url
            else:
                # 上传失败：记录需要移除的路径
                logger.warning(f"[发布Agent] 正文图上传失败，将从HTML移除: {local_path[:60] or url[:60]}")
                if local_path:
                    failed_paths.add(local_path)
                    failed_paths.add(local_path.replace("\\", "/"))
                if url:
                    failed_paths.add(url)

        # 替换HTML中成功上传的图片src
        final_html = html
        for original, wx_url in path_to_wx.items():
            final_html = final_html.replace(original, wx_url)
            final_html = final_html.replace(
                original.replace("\\", "/"), wx_url
            )

        # 移除上传失败的图片及其说明
        if failed_paths:
            final_html = self._remove_failed_images_and_captions(final_html, failed_paths)

        return final_html

    def _remove_failed_images_and_captions(self, html: str, failed_paths: set) -> str:
        """从HTML中移除上传失败的 <img> 标签及其紧随的说明 <p> 标签"""
        # 匹配 <img ...> 标签
        img_pattern = re.compile(r'<img[^>]*src="([^"]*)"[^>]*/?>', re.IGNORECASE)

        result_parts = []
        last_end = 0

        for match in img_pattern.finditer(html):
            src = match.group(1)
            is_failed = False
            for fp in failed_paths:
                if fp in src or src in fp:
                    is_failed = True
                    break

            if is_failed:
                # 添加匹配前的内容
                result_parts.append(html[last_end:match.start()])

                # 检查 <img> 后面是否紧跟一个图片说明 <p> 标签
                after_img = html[match.end():]
                # 跳过空白字符
                stripped = after_img.lstrip()
                leading_ws = len(after_img) - len(stripped)

                # 匹配 <p style="...color:#999999...">说明文字</p> 或类似的说明段
                caption_pattern = re.compile(
                    r'<p[^>]*style="[^"]*color:[^"]*#999[^"]*"[^>]*>.*?</p>',
                    re.IGNORECASE | re.DOTALL
                )
                caption_match = caption_pattern.match(stripped)
                if caption_match:
                    # 跳过 <img> + 空白 + <p>说明</p>
                    last_end = match.end() + leading_ws + caption_match.end()
                    logger.info("[发布Agent] 移除失败图片及其说明文字")
                else:
                    # 只跳过 <img>
                    last_end = match.end()
                    logger.info("[发布Agent] 移除失败图片（无说明文字）")
            # 如果不是失败的图片，正常处理（不跳过）
            else:
                pass

        result_parts.append(html[last_end:])
        return "".join(result_parts)

    async def _upload_inline_image(self, image_path: str, token: str) -> Optional[Dict]:
        """上传正文图到微信素材库"""
        path_obj = Path(image_path)
        if not path_obj.exists():
            return None

        url = f"{self.base_url}/cgi-bin/material/add_material"
        params = {"access_token": token, "type": "image"}

        try:
            http = await self._get_http()
            with open(path_obj, "rb") as f:
                files = {"media": (path_obj.name, f, "image/jpeg")}
                response = await http.post(url, params=params, files=files)
            data = response.json()

            if "url" in data or "media_id" in data:
                return {
                    "media_id": data.get("media_id"),
                    "url": data.get("url", ""),
                }
            else:
                logger.error(f"[发布Agent] 正文图上传失败: {data}")
                return None

        except Exception as e:
            logger.error(f"[发布Agent] 正文图上传异常: {e}")
            return None

    async def _download_temp_image(self, url: str) -> Optional[str]:
        """临时下载图片到本地（统一转换为标准JPEG，确保微信兼容）"""
        try:
            http = await self._get_http()
            response = await http.get(url, follow_redirects=True)
            response.raise_for_status()

            # 跳过 SVG
            content_type = response.headers.get("content-type", "")
            if "svg" in content_type or ".svg" in url.lower():
                logger.warning(f"[发布Agent] 跳过SVG图片: {url[:60]}")
                return None

            # 使用 PIL 验证并转换为标准 JPEG
            from io import BytesIO
            from PIL import Image as PILImage

            try:
                img = PILImage.open(BytesIO(response.content))
                img.load()
            except Exception as e:
                logger.warning(f"[发布Agent] 图片解析失败: {url[:60]} | {e}")
                return None

            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            elif img.mode == "L":
                img = img.convert("RGB")

            import tempfile
            with tempfile.NamedTemporaryFile(
                delete=False, suffix=".jpg", dir=tempfile.gettempdir()
            ) as f:
                img.save(f, "JPEG", quality=90, optimize=True)
                return f.name

        except Exception as e:
            logger.error(f"[发布Agent] 临时下载图片失败 {url[:60]}: {e}")
            return None

    async def _create_multi_draft(self, token: str, articles: List[Dict]) -> Dict:
        """创建多图文草稿"""
        url = f"{self.base_url}/cgi-bin/draft/add"
        params = {"access_token": token}

        draft_articles = []
        for article in articles:
            draft_articles.append({
                "title": article["title"],
                "author": "Aido",
                "digest": article.get("digest", ""),
                "content": article["content"],
                "content_source_url": "",
                "thumb_media_id": article.get("thumb_media_id", ""),
                "need_open_comment": 1,
                "only_fans_can_comment": 0,
            })

        payload = {"articles": draft_articles}

        try:
            http = await self._get_http()
            response = await http.post(url, params=params, json=payload)
            data = response.json()

            if "media_id" in data:
                return {"success": True, "media_id": data["media_id"]}
            else:
                errcode = data.get("errcode", "unknown")
                errmsg = data.get("errmsg", "unknown")
                logger.error(f"[发布Agent] 草稿创建失败: [{errcode}] {errmsg}")
                return {"success": False, "error": f"[{errcode}] {errmsg}"}

        except Exception as e:
            logger.error(f"[发布Agent] 草稿创建请求异常: {e}")
            return {"success": False, "error": str(e)}

    # ==================== Access Token ====================

    async def _get_access_token(self) -> Optional[str]:
        """获取/刷新 access_token"""
        import time as _time

        current_time = _time.time()
        if self._access_token and current_time < self._token_expires_at:
            return self._access_token

        url = f"{self.base_url}/cgi-bin/token"
        params = {
            "grant_type": "client_credential",
            "appid": self.app_id,
            "secret": self.app_secret,
        }

        try:
            http = await self._get_http()
            response = await http.get(url, params=params)
            data = response.json()

            if "access_token" in data:
                self._access_token = data["access_token"]
                self._token_expires_at = current_time + data.get("expires_in", 7200) - 300
                logger.debug("[发布Agent] access_token 刷新成功")
                return self._access_token
            else:
                errcode = data.get("errcode", "unknown")
                errmsg = data.get("errmsg", "unknown")
                logger.error(f"[发布Agent] 获取token失败: [{errcode}] {errmsg}")
                return None

        except Exception as e:
            logger.error(f"[发布Agent] access_token 请求异常: {e}")
            return None

    # ==================== 输出 ====================

    def _print_draft_summary(self, articles: List[Dict], media_id: str):
        """打印草稿摘要"""
        from rich.console import Console
        from rich.table import Table

        console = Console()
        table = Table(title=f"📝 草稿创建成功 [media_id: {media_id}]", show_header=True)
        table.add_column("位置", style="bold")
        table.add_column("标题", style="cyan")
        table.add_column("封面ID", style="dim")

        for article in articles:
            pos = article.get("position", "?")
            pos_label = "🔥 头条" if pos == "headline" else "📰 次条"
            table.add_row(
                pos_label,
                article.get("title", "?")[:50],
                article.get("thumb_media_id", "(无)")[:20],
            )

        console.print(table)

    async def _save_publish_record(self, media_id: str, articles: List[Dict]):
        """保存发布记录"""
        records_dir = Path(self.get_config("project_root", ".")) / "data" / "analytics"
        records_dir.mkdir(parents=True, exist_ok=True)

        today = datetime.now().strftime("%Y-%m-%d")
        record_file = records_dir / f"publish_records_{today}.json"

        record = {
            "timestamp": datetime.now().isoformat(),
            "media_id": media_id,
            "article_count": len(articles),
            "articles": [{"title": a["title"], "position": a.get("position")} for a in articles],
        }

        existing = []
        if record_file.exists():
            with open(record_file, "r", encoding="utf-8") as f:
                existing = json.load(f)
        existing.append(record)

        with open(record_file, "w", encoding="utf-8") as f:
            json.dump(existing, f, ensure_ascii=False, indent=2)

    async def cleanup(self):
        if self.http_client:
            await self.http_client.aclose()
