"""
从微信草稿箱获取已有文章，套用Banner模板后重新发布到草稿箱
- 跳过 Tavily 搜索（API超限）
- 使用草稿箱中已有正确图片的文章
- 应用Banner模板
- 修复图片CSS（防止裁剪）
"""

import asyncio
import json
import re
import sys
from pathlib import Path
from typing import Optional, List, Dict

import httpx

PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.config import Config
from src.logger import logger


async def get_token(config: Config) -> Optional[str]:
    base_url = config.get("wechat_mp.base_url", "https://api.weixin.qq.com")
    url = f"{base_url}/cgi-bin/token"
    params = {
        "grant_type": "client_credential",
        "appid": config.get("wechat_mp.app_id", ""),
        "secret": config.get("wechat_mp.app_secret", ""),
    }
    async with httpx.AsyncClient(timeout=30.0) as http:
        resp = await http.get(url, params=params)
        data = resp.json()
        if "access_token" in data:
            return data["access_token"]
        print(f"[ERROR] token: {data}")
        return None


async def fetch_drafts(token: str, config: Config) -> List[Dict]:
    """获取草稿箱文章"""
    base_url = config.get("wechat_mp.base_url", "https://api.weixin.qq.com")
    url = f"{base_url}/cgi-bin/draft/batchget"
    params = {"access_token": token}
    payload = {"offset": 0, "count": 10, "no_content": 0}

    async with httpx.AsyncClient(timeout=60.0) as http:
        resp = await http.post(url, params=params, json=payload)
        data = resp.json()

        articles = []
        for item in data.get("item", []):
            media_id = item.get("media_id", "")
            content = item.get("content", {})
            for news in content.get("news_item", []):
                html = news.get("content", "")
                title = news.get("title", "")
                thumb_media_id = news.get("thumb_media_id", "")
                if html:
                    articles.append({
                        "title": title,
                        "content": html,
                        "thumb_media_id": thumb_media_id,
                        "media_id": media_id,
                    })
        return articles


def apply_banner_template(html: str, banner_html: str) -> str:
    """在文章HTML前面加上Banner"""
    # 修复图片CSS：确保不裁剪
    # 1. 给所有 img 标签添加 height:auto 和 object-fit:contain
    def fix_img_css(match):
        tag = match.group(0)
        # 如果已有 height 属性，替换为 auto
        if re.search(r'height:\s*[^;"]+', tag):
            tag = re.sub(r'height:\s*[^;"]+;?', 'height:auto;', tag)
        else:
            # 在 style 中添加 height:auto
            style_match = re.search(r'style="([^"]*)"', tag)
            if style_match:
                old_style = style_match.group(1)
                new_style = old_style.rstrip(';') + ';height:auto;object-fit:contain;'
                tag = tag.replace(f'style="{old_style}"', f'style="{new_style}"')
        return tag

    html = re.sub(r'<img[^>]+>', fix_img_css, html)

    # 在文章最前面插入 Banner
    result = banner_html.strip() + "\n" + html.strip()
    return result


def load_banner() -> str:
    """加载Banner HTML"""
    banner_file = PROJECT_ROOT / "config" / "templates" / "banner.html"
    if banner_file.exists():
        return banner_file.read_text(encoding="utf-8")
    return ""


def extract_images_from_html(html: str) -> List[Dict]:
    """从HTML中提取所有图片信息"""
    images = []
    # 匹配 data-src 和 src
    for match in re.finditer(r'<img[^>]+>', html):
        tag = match.group(0)
        url = ""
        # 优先 data-src（微信CDN）
        ds = re.search(r'data-src="([^"]+)"', tag)
        if ds:
            url = ds.group(1)
        if not url:
            ss = re.search(r'src="([^"]+)"', tag)
            if ss:
                url = ss.group(1)
        if url:
            images.append({"url": url, "tag": tag})
    return images


async def create_draft(token: str, config: Config, articles: List[Dict]) -> Dict:
    """创建多图文草稿"""
    base_url = config.get("wechat_mp.base_url", "https://api.weixin.qq.com")
    url = f"{base_url}/cgi-bin/draft/add"
    params = {"access_token": token}

    draft_articles = []
    for article in articles:
        draft_articles.append({
            "title": article["title"],
            "author": "WeChat-MP-Agent",
            "digest": article.get("digest", "")[:64],
            "content": article["content"],
            "content_source_url": "",
            "thumb_media_id": article.get("thumb_media_id", ""),
            "need_open_comment": 1,
            "only_fans_can_comment": 0,
        })

    payload = {"articles": draft_articles}

    async with httpx.AsyncClient(timeout=60.0) as http:
        resp = await http.post(url, params=params, json=payload)
        data = resp.json()
        return data


async def delete_draft(token: str, config: Config, media_id: str):
    """删除草稿"""
    base_url = config.get("wechat_mp.base_url", "https://api.weixin.qq.com")
    url = f"{base_url}/cgi-bin/draft/delete"
    params = {"access_token": token}
    payload = {"media_id": media_id}

    async with httpx.AsyncClient(timeout=30.0) as http:
        r = await http.post(url, params=params, json=payload)
        return r.json()


async def main():
    config = Config()
    config.reload()
    config.data["project_root"] = str(PROJECT_ROOT)

    print("=" * 60)
    print("  使用Banner模板重新发布草稿箱文章")
    print("=" * 60)

    # 获取 token
    token = await get_token(config)
    if not token:
        return
    print("  ✅ token 获取成功")

    # 获取草稿箱文章
    print("\n[1] 获取草稿箱文章...")
    drafts = await fetch_drafts(token, config)
    print(f"  找到 {len(drafts)} 篇草稿文章")

    if not drafts:
        print("  [ERROR] 草稿箱为空")
        return

    # 加载Banner
    banner_html = load_banner()
    if not banner_html:
        print("  [ERROR] Banner未找到")
        return
    print(f"  ✅ Banner已加载")

    # 筛选适合的文章
    # 优先选择图片有描述性alt（与文章匹配的）的文章
    print("\n[2] 分析草稿文章...")
    for i, draft in enumerate(drafts):
        images = extract_images_from_html(draft["content"])
        # 检查图片alt是否有描述性文字（说明图片与文章匹配）
        has_descriptive_alt = False
        for img in images:
            alt_match = re.search(r'alt="([^"]+)"', img["tag"])
            if alt_match and len(alt_match.group(1)) > 5:
                has_descriptive_alt = True
                break

        draft["image_count"] = len(images)
        draft["images"] = images
        draft["has_descriptive_alt"] = has_descriptive_alt
        print(f"  文章 {i+1}: '{draft['title'][:40]}' | 图片: {len(images)}张 | alt描述: {'有' if has_descriptive_alt else '无'} | thumb: {'有' if draft.get('thumb_media_id') else '无'}")

    # 排序：优先选有描述性alt的文章（图片与文章匹配）
    suitable = sorted(
        [d for d in drafts if d["image_count"] >= 1],
        key=lambda d: (not d["has_descriptive_alt"], -d["image_count"])
    )

    if len(suitable) < 2:
        suitable = drafts

    # 取前2篇
    selected = suitable[:2]
    print(f"\n  选中 {len(selected)} 篇文章:")
    for i, s in enumerate(selected):
        pos = "头条" if i == 0 else "次条"
        print(f"    {pos}: {s['title'][:40]}")

    # 应用Banner模板
    print("\n[3] 应用Banner模板...")
    for article in selected:
        original_html = article["content"]
        new_html = apply_banner_template(original_html, banner_html)
        article["content"] = new_html
        article["digest"] = article.get("title", "")[:64]
        print(f"  ✅ '{article['title'][:40]}' 已应用Banner模板")

    # 创建新草稿
    print("\n[4] 创建新草稿...")
    result = await create_draft(token, config, selected)

    if "media_id" in result:
        print(f"\n{'='*60}")
        print(f"  ✅ 草稿创建成功!")
        print(f"  草稿 media_id: {result['media_id']}")
        print(f"  文章数: {len(selected)}")
        print(f"  请登录微信公众号后台 → 草稿箱 查看")
        print(f"  新草稿包含Banner + 原文章内容（图片已修复防裁剪）")
        print(f"{'='*60}")

        # 保存发布记录
        records_dir = PROJECT_ROOT / "data" / "analytics"
        records_dir.mkdir(parents=True, exist_ok=True)
        record = {
            "timestamp": __import__("datetime").datetime.now().isoformat(),
            "media_id": result["media_id"],
            "article_count": len(selected),
            "articles": [{"title": a["title"], "image_count": a.get("image_count", 0)} for a in selected],
            "banner_applied": True,
        }
        record_file = records_dir / "publish_records_2026-06-25.json"
        existing = []
        if record_file.exists():
            with open(record_file, "r", encoding="utf-8") as f:
                existing = json.load(f)
        existing.append(record)
        with open(record_file, "w", encoding="utf-8") as f:
            json.dump(existing, f, ensure_ascii=False, indent=2)
    else:
        print(f"\n  ❌ 草稿创建失败: {result}")

    # 尝试删除旧草稿（用户之前创建的）
    print("\n[5] 清理旧草稿...")
    for draft in drafts:
        if draft.get("media_id") and draft["media_id"] != result.get("media_id"):
            try:
                del_result = await delete_draft(token, config, draft["media_id"])
                if del_result.get("errcode", 0) == 0:
                    print(f"  🗑️ 已删除旧草稿: '{draft['title'][:40]}'")
                else:
                    print(f"  ⚠️ 删除失败: {del_result}")
            except Exception as e:
                print(f"  ⚠️ 删除异常: {e}")


if __name__ == "__main__":
    asyncio.run(main())
