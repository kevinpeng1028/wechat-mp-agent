"""
获取微信公众号草稿箱/已发布文章HTML作为模板
"""

import asyncio
import json
import re
import sys
from pathlib import Path
from typing import Optional

import httpx

PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.config import Config


async def get_access_token(config: Config) -> Optional[str]:
    base_url = config.get("wechat_mp.base_url", "https://api.weixin.qq.com")
    app_id = config.get("wechat_mp.app_id", "")
    app_secret = config.get("wechat_mp.app_secret", "")

    url = f"{base_url}/cgi-bin/token"
    params = {"grant_type": "client_credential", "appid": app_id, "secret": app_secret}

    async with httpx.AsyncClient(timeout=30.0) as http:
        resp = await http.get(url, params=params)
        data = resp.json()
        if "access_token" in data:
            return data["access_token"]
        print(f"[ERROR] token失败: [{data.get('errcode')}] {data.get('errmsg')}")
        return None


async def main():
    config = Config()
    config.reload()

    print("=" * 60)
    print("  获取微信公众号模板")
    print("=" * 60)

    token = await get_access_token(config)
    if not token:
        return
    print("  ✅ token 获取成功")

    base_url = config.get("wechat_mp.base_url", "https://api.weixin.qq.com")

    # 方法1: 草稿箱
    print("\n[1] 尝试获取草稿箱...")
    url = f"{base_url}/cgi-bin/draft/batchget"
    params = {"access_token": token}
    payload = {"offset": 0, "count": 10, "no_content": 0}

    all_articles = []

    async with httpx.AsyncClient(timeout=60.0) as http:
        resp = await http.post(url, params=params, json=payload)
        data = resp.json()
        print(f"  响应: total_count={data.get('total_count')}, item_count={data.get('item_count')}")

        # 草稿箱返回的是 item 数组
        items = data.get("item", [])
        for item in items:
            content = item.get("content", {})
            news_items = content.get("news_item", [])
            for news in news_items:
                html = news.get("content", "")
                title = news.get("title", "")
                if html:
                    all_articles.append({"title": title, "content": html})
                    print(f"  📄 草稿文章: {title[:50]}")

    # 方法2: 素材库 (material/batchget_material)
    if not all_articles:
        print("\n[2] 尝试获取素材库永久图片素材...")
        url = f"{base_url}/cgi-bin/material/batchget_material"
        params = {"access_token": token}
        payload = {"type": "image", "offset": 0, "count": 20}

        async with httpx.AsyncClient(timeout=60.0) as http:
            resp = await http.post(url, params=params, json=payload)
            data = resp.json()
            print(f"  素材库: total_count={data.get('total_count')}, item_count={data.get('item_count')}")

            materials = data.get("item", [])
            banner_images = []
            for m in materials:
                murl = m.get("url", "")
                mname = m.get("name", "")
                if murl:
                    banner_images.append({"url": murl, "name": mname})
                    print(f"  🖼️ 素材: {mname[:30]} | {murl[:60]}")

    # 方法3: 已发布文章
    print("\n[3] 尝试获取已发布文章...")
    url = f"{base_url}/cgi-bin/freepublish/batchget"
    payload = {"offset": 0, "count": 5, "no_content": 0}

    async with httpx.AsyncClient(timeout=60.0) as http:
        resp = await http.post(url, params=params, json=payload)
        data = resp.json()
        errcode = data.get("errcode", 0)
        if errcode == 48001:
            print("  ⚠️ 未获得'已发布文章'API权限（48001），跳过")
        elif errcode != 0:
            print(f"  ⚠️ 获取失败: [{errcode}] {data.get('errmsg')}")
        else:
            items = data.get("item", [])
            for item in items:
                content = item.get("content", {})
                for news in content.get("news_item", []):
                    html = news.get("content", "")
                    title = news.get("title", "")
                    if html:
                        all_articles.append({"title": title, "content": html})
                        print(f"  📄 已发布: {title[:50]}")

    if all_articles:
        # 保存所有文章HTML
        template_dir = PROJECT_ROOT / "config" / "templates"
        template_dir.mkdir(parents=True, exist_ok=True)

        for i, article in enumerate(all_articles):
            safe_title = "".join(c for c in article["title"][:30] if c.isalnum() or c in "_ ")
            ref_file = template_dir / f"ref_{i}_{safe_title}.html"
            with open(ref_file, "w", encoding="utf-8") as f:
                f.write(article["content"])
            print(f"\n  💾 已保存: {ref_file}")

        # 保存为模板
        template_file = template_dir / "templates.json"
        templates_data = {}
        if template_file.exists():
            with open(template_file, "r", encoding="utf-8") as f:
                try:
                    templates_data = json.load(f)
                except json.JSONDecodeError:
                    pass

        templates_data["wechat_published_template"] = {
            "name": "wechat_published_template",
            "source": "公众号已发布/草稿文章",
            "html": all_articles[0]["content"],
            "font_size": "16px",
            "line_height": "1.9",
            "letter_spacing": "0.5px",
            "paragraph_spacing": "18px",
            "image_style": "default",
            "enabled": True,
            "created_at": "",
        }

        with open(template_file, "w", encoding="utf-8") as f:
            json.dump(templates_data, f, ensure_ascii=False, indent=2)

        print(f"\n  ✅ 模板已保存到 templates.json")
        print(f"  模板名称: wechat_published_template (已启用)")
    else:
        print("\n  ⚠️ 未找到任何含HTML的文章")
        print("  如果草稿箱中有文章，可能内容为空（只有标题+封面）")

    print(f"\n{'='*60}")


if __name__ == "__main__":
    asyncio.run(main())
