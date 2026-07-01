from src.formatter.formatting_agent import FormattingAgent
from src.formatter.template_manager import TemplateManager
from src.writer.writing_agent import WritingAgent


def test_default_image_render_has_no_caption_metadata():
    manager = object.__new__(TemplateManager)
    manager.config = {}
    manager.default_style = {}
    html = manager._render_default(
        title="BTS V动态",
        summary="",
        intro="BTS V的最新动态公开了。",
        body_paragraphs=["相关内容公开后，很快有了新的讨论。"],
        images=[{
            "url": "https://cdn.example.com/photo.jpg",
            "filename": "tavily_20260701_095721_001.jpg",
            "caption": "bts-jungkook-headliner-640x960-2 (1)",
            "description": "Tavily technical image description",
        }],
        ending="后续活动仍值得继续关注。",
    )

    assert "<img" in html
    assert "tavily_20260701_095721_001.jpg" not in html
    assert "bts-jungkook-headliner-640x960-2 (1)" not in html
    assert "Tavily technical image description" not in html
    assert "figcaption" not in html.lower()


def test_sanitizer_removes_legacy_figcaption_and_gray_technical_text():
    formatter = object.__new__(FormattingAgent)
    html = formatter._sanitize_html(
        '<img src="https://cdn.example.com/photo.jpg">'
        '<figcaption>local_file.jpg</figcaption>'
        '<p style="font-size:13px;color:#999999;text-align:center;">'
        'tavily_001.jpg</p>'
    )

    assert "local_file.jpg" not in html
    assert "tavily_001.jpg" not in html
    assert "figcaption" not in html.lower()


def test_writer_prompt_uses_mobile_kpop_newsletter_style_without_ai_news_tone():
    writer = object.__new__(WritingAgent)
    writer.config = {
        "writer_agent": {
            "banned_phrases": ["引发广泛关注", "值得注意的是"],
            "safe_expressions": ["这次更新", "状态"],
            "format_rules": {
                "no_markdown_headings": True,
                "no_hashtags": True,
            },
        }
    }
    topic = {
        "title": "IVE Wonyoung airport fashion goes viral",
        "source_name": "Koreaboo",
        "content": "IVE member Wonyoung appeared at the airport.",
    }
    system_prompt = writer._get_system_prompt()
    prompt = writer._build_writing_prompt(topic, [topic], [])

    assert "中文韩娱资讯公众号" in system_prompt
    assert "多用短句" in system_prompt
    assert "不模仿或复制任何具体账号" in system_prompt
    assert "轻快自然的韩娱快讯" in prompt
    assert "未确认的服装、动作、表情" in prompt
    assert "引发广泛关注" in prompt  # 明确列为禁用新闻稿腔
