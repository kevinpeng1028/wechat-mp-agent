"""
独立发布脚本 - 使用已生成的排版HTML文章，直接推送到微信公众号草稿箱
跳过选题/写作/配图/排版步骤（因 Tavily API 超限无法重新搜索）
"""

import asyncio
import re
import sys
from pathlib import Path

# 设置项目根目录
PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.config import Config
from src.publisher.publisher_agent import PublisherAgent
from src.logger import logger


def load_formatted_article(html_path: Path) -> dict:
    """从排版后的HTML文件中提取文章信息和图片"""
    content = html_path.read_text(encoding="utf-8")

    # 从HTML注释中提取元数据
    title_match = re.search(r"<!-- 标题: (.+?) -->", content)
    position_match = re.search(r"<!-- 位置: (.+?) -->", content)

    title = title_match.group(1).strip() if title_match else html_path.stem
    position = position_match.group(1).strip() if position_match else "unknown"

    # 提取摘要（从对应的txt文件）
    txt_name = html_path.name.replace("formatted_", "draft_").replace(".html", ".txt")
    txt_path = html_path.parent / txt_name
    summary = ""
    if txt_path.exists():
        txt_content = txt_path.read_text(encoding="utf-8")
        summary_match = re.search(r"摘要: (.+)", txt_content)
        if summary_match:
            summary = summary_match.group(1).strip()

    # 提取HTML中的所有图片URL
    img_urls = re.findall(r'<img[^>]+src="([^"]+)"', content)

    # 构建图片信息列表
    valid_images = [{"url": url} for url in img_urls]

    # 第一张图作为封面
    cover_image = {"url": img_urls[0]} if img_urls else None

    return {
        "is_success": True,
        "title": title,
        "summary": summary,
        "html_content": content,
        "cover_image": cover_image,
        "valid_images": valid_images,
        "position": position,
    }


async def main():
    # 加载配置
    config = Config()
    config.reload()

    # 设置 project_root
    project_root = str(PROJECT_ROOT)
    config.data["project_root"] = project_root

    # 找到排版后的HTML文件
    articles_dir = PROJECT_ROOT / "data" / "articles"
    html_files = sorted(articles_dir.glob("formatted_*.html"))

    if not html_files:
        print("[ERROR] 没有找到排版后的HTML文件")
        return

    print(f"\n找到 {len(html_files)} 篇排版好的文章:")
    for i, f in enumerate(html_files):
        print(f"  {i+1}. {f.name}")

    # 只取最新的2篇（头条+次条）
    # 按 timestamp 排序，取最新的一组
    html_files = html_files[-2:] if len(html_files) >= 2 else html_files

    # 构建 formatted_articles
    formatted_articles = []
    for html_path in html_files:
        article = load_formatted_article(html_path)
        formatted_articles.append(article)
        print(f"\n  -> {article['position']}: {article['title']}")
        print(f"     摘要: {article['summary'][:60]}...")
        print(f"     图片: {len(article['valid_images'])} 张")

    # 创建发布Agent
    print("\n[发布] 初始化 PublisherAgent...")
    publisher = PublisherAgent(config.data)

    # 构建上下文
    context = {
        "formatted_articles": formatted_articles,
    }

    # 执行发布
    print("[发布] 开始上传图片并创建草稿...\n")
    result = await publisher.execute(context)

    if result.is_success:
        print(f"\n{'='*60}")
        print(f"  ✅ 发布成功!")
        print(f"  草稿 media_id: {result.output.get('draft_media_id')}")
        print(f"  文章数: {result.output.get('article_count')}")
        print(f"  请登录微信公众号后台 → 草稿箱 查看")
        print(f"{'='*60}")
    else:
        print(f"\n{'='*60}")
        print(f"  ❌ 发布失败: {result.error}")
        print(f"{'='*60}")

    # 清理
    await publisher.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
