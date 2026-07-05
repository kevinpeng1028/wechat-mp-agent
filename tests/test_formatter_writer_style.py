import asyncio
from types import SimpleNamespace

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
    assert "目标约400字" in prompt
    assert "理想350-500字" in prompt
    assert "未确认的服装、动作、表情" in prompt
    assert "引发广泛关注" in prompt  # 明确列为禁用新闻稿腔
    assert "原文事实清单" in prompt
    assert "事实内容保持85%-90%以上一致" in prompt
    assert "不新增事实" in prompt


def test_writer_fidelity_rejects_invented_reactions_company_and_macro_analysis():
    writer = object.__new__(WritingAgent)
    writer.config = {"writer_agent": {"banned_phrases": []}}
    source = [{
        "title": "IVE releases comeback teaser",
        "content": "IVE released a comeback teaser on July 3.",
    }]
    parsed = {
        "title": "IVE回归预告公开",
        "summary": "",
        "content_text": (
            "IVE公开了回归预告。粉丝很快在评论区热议。"
            "HYBE随后作出回应，这也改变了行业格局。"
        ),
    }
    issues = writer._check_source_fidelity(parsed, source)

    assert any("网友/粉丝反应" in issue for issue in issues)
    assert any("公司别名待核对: hybe" in issue for issue in issues)
    assert any("AI宏观判断" in issue for issue in issues)


def test_writer_fidelity_allows_reactions_only_when_source_reports_them():
    writer = object.__new__(WritingAgent)
    writer.config = {"writer_agent": {"banned_phrases": []}}
    source = [{
        "title": "IVE releases comeback teaser",
        "content": "IVE released a teaser. Fans react positively to the release.",
    }]
    parsed = {
        "title": "IVE回归预告公开",
        "summary": "",
        "content_text": "IVE公开了回归预告，原文提到粉丝也给出了积极反应。",
    }
    assert writer._check_source_fidelity(parsed, source) == []

    korean_source = [{
        "title": "아이브 티저 공개",
        "content": "팬들은 새 티저에 긍정적인 반응을 보였다.",
    }]
    assert writer._check_source_fidelity(parsed, korean_source) == []


def test_itzy_proper_names_and_concert_fan_reaction_are_production_safe():
    writer = object.__new__(WritingAgent)
    writer.config = {"writer_agent": {"banned_phrases": []}}
    content = (
        "ITZY完成了《TUNNEL VISION》高雄站演出，成员感谢MIDZY的支持。"
        "现场粉丝反应热烈，后续巡演日程将继续进行。"
    ) * 4
    check = writer._strict_check(
        {"title": "ITZY高雄巡演结束", "summary": "", "content_text": content}
    )
    assert check["passed"]
    source = [{
        "title": "ITZY 가오슝 월드투어",
        "content": "멤버들은 현장 팬의 뜨거운 반응에 감사했다.",
    }]
    assert not any(
        "网友/粉丝反应" in issue
        for issue in writer._check_source_fidelity(
            {"title": "ITZY巡演", "summary": "", "content_text": content},
            source,
        )
    )


def test_newjeans_and_ador_korean_aliases_are_not_new_entities():
    writer = object.__new__(WritingAgent)
    writer.config = {"writer_agent": {"banned_phrases": []}}
    issues = writer._check_source_fidelity(
        {
            "title": "ADOR与NewJeans最新动态",
            "summary": "",
            "content_text": "ADOR公开了与NewJeans相关的现有安排。",
        },
        [{"title": "어도어 뉴진스 관련 소식", "content": "어도어와 뉴진스"}],
    )
    assert not any("公司别名待核对: ador" in issue for issue in issues)
    assert not any("NEWJEANS" in issue for issue in issues)


def test_additionally_is_only_a_soft_warning():
    writer = object.__new__(WritingAgent)
    writer.config = {"writer_agent": {"banned_phrases": ["此外"]}}
    result = writer._strict_check({
        "title": "TWS最新动态",
        "summary": "",
        "content_text": ("TWS公开了新的活动安排。此外，相关日程将按计划推进。" * 8),
    })
    assert result["passed"]
    assert any("此外" in warning for warning in result["warnings"])


def test_two_unparseable_generations_use_safe_fallback_article():
    class FakeCompletions:
        def __init__(self):
            self.calls = 0

        async def create(self, **kwargs):
            self.calls += 1
            return SimpleNamespace(
                choices=[SimpleNamespace(
                    message=SimpleNamespace(content="not valid json")
                )]
            )

    writer = object.__new__(WritingAgent)
    writer.config = {
        "writer_agent": {"banned_phrases": [], "safe_expressions": []},
        "writing": {"allow_short_news_when_facts_limited": True},
        "llm": {"model": "test", "temperature": {"writing": 0.1}},
    }
    completions = FakeCompletions()
    writer.llm_client = SimpleNamespace(
        chat=SimpleNamespace(completions=completions)
    )
    topic = {
        "title": "ITZY world tour update",
        "original_title": "ITZY world tour update",
        "content": "\n".join([
            "ITZY completed a scheduled world tour performance for local fans."
            for _ in range(7)
        ]),
        "_tavily_images": [{"url": "one.jpg"}, {"url": "two.jpg"}],
    }
    result = asyncio.run(writer._write_single(topic, {}))

    assert completions.calls == 2
    assert result["is_success"]
    assert result["production_fallback"] is True
    assert len(result["content_text"]) >= 100
    assert len(__import__("re").findall(r"[\u4e00-\u9fff]", result["content_text"])) >= 100


def test_writer_fidelity_allows_no_reaction_when_source_and_output_have_none():
    writer = object.__new__(WritingAgent)
    writer.config = {"writer_agent": {"banned_phrases": []}}
    source = [{"title": "아이브 음악방송 출연", "content": "무대를 공개했다."}]
    parsed = {
        "title": "IVE亮相音乐节目",
        "summary": "",
        "content_text": "IVE公开了最新音乐节目舞台，相关活动按计划进行。",
    }
    assert writer._check_source_fidelity(parsed, source) == []


def test_writer_alias_mapping_treats_korean_and_english_names_as_same_entity():
    writer = object.__new__(WritingAgent)
    writer.config = {"writer_agent": {"banned_phrases": []}}
    cases = [
        ("방탄소년단 진 새 소식", "BTS Jin公开新动态"),
        ("방탄소년단 새 소식", "BTS公开新动态"),
        ("아이브 음악방송", "IVE亮相音乐节目"),
        ("있지 월드투어", "ITZY继续世界巡演"),
    ]
    for source_title, output_text in cases:
        issues = writer._check_source_fidelity(
            {"title": output_text, "summary": "", "content_text": output_text},
            [{"title": source_title, "content": source_title}],
        )
        assert not any("未提及艺人" in issue for issue in issues)


def test_enhypen_member_aliases_are_bidirectional_and_reject_new_members():
    writer = object.__new__(WritingAgent)
    writer.config = {"writer_agent": {"banned_phrases": []}}
    source = [{
        "title": "엔하이픈 정원 제이 제이크 성훈 월드투어",
        "content": "정원 제이 제이크 성훈이 공연에 참여했다.",
    }]
    allowed = {
        "title": "ENHYPEN世界巡演",
        "summary": "",
        "content_text": "ENHYPEN成员Jungwon、Jay、Jake和Sunghoon参加了演出。",
    }
    assert writer._check_source_fidelity(allowed, source) == []

    assert writer._check_source_fidelity(
        {"title": "Jake动态", "summary": "", "content_text": "Jake参加演出。"},
        [{"title": "제이크 공연", "content": "제이크가 공연했다."}],
    ) == []

    issues = writer._check_source_fidelity(
        {
            "title": "巡演动态",
            "summary": "",
            "content_text": "Jake、Jay、Jungwon和Sunghoon参加演出。",
        },
        [{"title": "ENHYPEN巡演", "content": "组合公开巡演消息。"}],
    )
    assert any("ENHYPEN_JAKE" in issue for issue in issues)
    assert any("ENHYPEN_JAY" in issue for issue in issues)


def test_alias_detection_is_per_article_and_excludes_navigation_pollution():
    writer = object.__new__(WritingAgent)
    first = [{
        "title": "엔하이픈 정원 제이크 월드투어",
        "content": "엔하이픈이 월드투어를 시작했다.",
    }]
    aliases, contexts = writer._detect_source_entities_with_context(first)
    assert "ENHYPEN" in aliases
    assert "ENHYPEN_JUNGWON" in aliases
    assert "BTS" not in aliases
    assert "Jungkook" not in aliases
    assert "Jimin" not in aliases
    assert set(contexts) <= {
        "title", "snippet", "meta_description", "article_body_cleaned"
    }

    second = [{"title": "i-dle MV teaser", "content": "i-dle released a teaser."}]
    second_aliases, _ = writer._detect_source_entities_with_context(second)
    assert "BTS_JIN" not in second_aliases
    assert "ENHYPEN" not in second_aliases


def test_short_news_mode_accepts_191_chars_but_normal_mode_prefers_more():
    writer = object.__new__(WritingAgent)
    writer.config = {
        "writing": {
            "absolute_min_chars": 160,
            "short_news_mode_max_chars": 300,
            "ideal_max_chars": 500,
        },
        "writer_agent": {"banned_phrases": []},
    }
    parsed = {
        "title": "IVE活动动态",
        "summary": "",
        "content_text": "这是一条忠实整理的短讯。" * 17,
    }
    assert 160 <= len(parsed["content_text"]) <= 300
    assert writer._strict_check(parsed, short_news_mode=True)["passed"]

    production_short = dict(parsed, content_text="短讯" * 50)
    result = writer._strict_check(production_short, short_news_mode=True)
    assert result["passed"]
    assert result["warnings"]


def test_fact_rich_article_under_250_chars_requires_rewrite():
    writer = object.__new__(WritingAgent)
    writer.config = {
        "writing": {"absolute_min_chars": 160},
        "writer_agent": {"banned_phrases": []},
    }
    parsed = {
        "title": "ENHYPEN巡演",
        "summary": "",
        "content_text": "组合公开了巡演安排。" * 15,
    }
    assert len(parsed["content_text"]) < 250
    result = writer._strict_check(parsed, short_news_mode=False)
    assert result["passed"]
    assert any("生产短文模式" in issue for issue in result["warnings"])
