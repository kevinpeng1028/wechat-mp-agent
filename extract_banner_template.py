"""
从草稿箱文章中提取Banner模板，创建可复用的排版模板
"""

import json
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))


def extract_banner_from_article(html: str) -> dict:
    """从文章HTML中提取Banner区域和结构"""
    # 提取开头的 section（通常包含Banner图）
    banner_match = re.match(
        r'(<section[^>]*>.*?</section>)',
        html,
        re.DOTALL
    )

    banner_html = ""
    banner_img_url = ""

    if banner_match:
        banner_html = banner_match.group(1)
        # 提取Banner图片URL
        img_match = re.search(r'data-src="([^"]+)"', banner_html)
        if img_match:
            banner_img_url = img_match.group(1)
        # 也尝试 src
        if not banner_img_url:
            img_match = re.search(r'src="([^"]+)"', banner_html)
            if img_match:
                banner_img_url = img_match.group(1)

    return {
        "banner_html": banner_html,
        "banner_img_url": banner_img_url,
        "has_banner": bool(banner_html),
    }


def create_template_with_banner(banner_html: str) -> str:
    """创建包含Banner的完整模板HTML"""
    template = f"""{banner_html}
<section style="margin:0;padding:0;">
{{INTRO}}
{{BODY_PARAGRAPHS}}
{{ENDING}}
</section>"""
    return template


def main():
    template_dir = PROJECT_ROOT / "config" / "templates"

    # 读取"周杰伦"文章（它包含了Banner模板）
    ref_file = template_dir / "ref_3_周杰伦新歌上线这次真的不太一样.html"
    if not ref_file.exists():
        print("[ERROR] 参考文章不存在")
        return

    html = ref_file.read_text(encoding="utf-8")

    # 提取Banner
    banner_info = extract_banner_from_article(html)
    print(f"Banner found: {banner_info['has_banner']}")
    print(f"Banner URL: {banner_info['banner_img_url'][:80]}...")

    if not banner_info["has_banner"]:
        print("[ERROR] 未找到Banner")
        return

    # 创建模板HTML
    # Banner + 正文区域 + 占位符
    template_html = f"""{banner_info['banner_html']}
<section style="margin:0;padding:0;">
{{{{INTRO}}}}
{{{{BODY_PARAGRAPHS}}}}
{{{{ENDING}}}}
</section>"""

    # 保存模板
    template_file = template_dir / "templates.json"
    templates_data = {}
    if template_file.exists():
        with open(template_file, "r", encoding="utf-8") as f:
            try:
                templates_data = json.load(f)
            except json.JSONDecodeError:
                pass

    templates_data["wechat_banner_template"] = {
        "name": "wechat_banner_template",
        "source": "公众号草稿箱文章（含Banner）",
        "html": template_html,
        "font_size": "16px",
        "line_height": "1.9",
        "letter_spacing": "0.5px",
        "paragraph_spacing": "18px",
        "image_style": "default",
        "enabled": True,
        "created_at": "",
        "banner_img_url": banner_info["banner_img_url"],
        "has_banner": True,
    }

    with open(template_file, "w", encoding="utf-8") as f:
        json.dump(templates_data, f, ensure_ascii=False, indent=2)

    print(f"\n✅ 模板已保存: {template_file}")
    print(f"模板名称: wechat_banner_template (已启用)")
    print(f"Banner图片: {banner_info['banner_img_url'][:80]}...")

    # 也保存Banner HTML单独文件
    banner_file = template_dir / "banner.html"
    with open(banner_file, "w", encoding="utf-8") as f:
        f.write(banner_info["banner_html"])
    print(f"Banner HTML: {banner_file}")


if __name__ == "__main__":
    main()
