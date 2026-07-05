import asyncio
import importlib
import sys
import time
import types

from src.base_agent import AgentResult, AgentStatus
from src.image.image_agent import ImageAgent, _is_blocked_image
from src.topic.topic_agent import TopicAgent


def _orchestrator_class():
    stubs = {
        "src.trigger.scheduler": "TriggerLayer",
        "src.writer.writing_agent": "WritingAgent",
        "src.formatter.formatting_agent": "FormattingAgent",
        "src.publisher.publisher_agent": "PublisherAgent",
        "src.feedback.feedback_agent": "FeedbackAgent",
    }
    for module_name, class_name in stubs.items():
        if module_name not in sys.modules:
            module = types.ModuleType(module_name)
            setattr(module, class_name, type(class_name, (), {}))
            sys.modules[module_name] = module
    module = importlib.import_module("src.orchestrator")
    return module.WeChatMPOrchestrator


def test_image_headers_and_invalid_filename_filter():
    headers = ImageAgent._headers_for_url(
        "https://www.allkpop.com/upload/image.jpg"
    )
    assert "Mozilla" in headers["User-Agent"]
    assert headers["Referer"] == "https://www.allkpop.com/"
    assert _is_blocked_image("https://cdn.example.com/xwhite30.png")


def test_daily_query_cache_avoids_second_tavily_call(tmp_path):
    agent = object.__new__(TopicAgent)
    agent.use_search_cache = True
    agent.max_tavily_queries = 10
    agent.freshness_hours = 24
    agent._actual_tavily_calls = 0
    agent._cache_hits = 0
    agent._tavily_budget = {
        "credits": 0, "cache_hits": 0, "hard_stop_triggered": False
    }
    agent._search_cache = {}
    agent._daily_cache_path = tmp_path / "cache.json"
    agent.get_config = lambda key, default=None: {
        "tavily.time_range": "24h",
    }.get(key, default)
    query = {
        "query": "site:soompi.com BTS latest",
        "source_name": "Soompi",
        "site": "soompi.com",
    }
    cache_key = f"{query['query']}|day"
    agent._search_cache[cache_key] = (
        time.time(),
        [{"title": "BTS update", "url": "https://example.com/bts"}],
    )

    first = asyncio.run(agent._tavily_search(query))
    second = asyncio.run(agent._tavily_search(query))

    assert first == second
    assert agent._actual_tavily_calls == 0
    assert agent._cache_hits == 2


def test_daily_query_cache_persists_between_agent_instances(tmp_path):
    cache_path = tmp_path / "search_cache_2026-06-30.json"
    first = object.__new__(TopicAgent)
    first.use_search_cache = True
    first._daily_cache_path = cache_path
    first._search_cache = {
        "query|day": (
            time.time(),
            [{
                "title": "BTS update",
                "url": "https://example.com/bts",
                "published_date_str": "2026-06-30",
            }],
        )
    }
    first._save_daily_search_cache()

    second = object.__new__(TopicAgent)
    second.use_search_cache = True
    second._daily_cache_path = cache_path
    second._search_cache = {}
    second._load_daily_search_cache()

    assert second._search_cache["query|day"][1][0]["title"] == "BTS update"


def test_tiered_search_respects_total_budget():
    agent = object.__new__(TopicAgent)
    agent.max_core_tavily_queries = 6
    agent.freshness_hours = 24
    agent.max_extra_tavily_queries = 4
    agent.min_candidates_before_extra = 3
    agent._actual_tavily_calls = 0
    agent._core_tavily_calls = 0
    agent._extra_tavily_calls = 0
    agent._emergency_tavily_calls = 0
    agent._cache_hits = 0
    agent.max_total_tavily_credits = 12
    agent._tavily_budget = {"credits": 0, "hard_stop_triggered": False}
    agent._seen_search_urls = set()
    agent.scoring = types.SimpleNamespace(
        _check_duplicate=lambda article: {"is_duplicate": False}
    )
    agent._filter_idol_centric_topics = lambda articles: articles
    agent.get_config = lambda key, default=None: "test-key"

    async def fake_search(query):
        agent._actual_tavily_calls += 1
        if query["budget_purpose"] == "core":
            agent._core_tavily_calls += 1
        elif query["budget_purpose"] == "emergency":
            agent._emergency_tavily_calls += 1
        else:
            agent._extra_tavily_calls += 1
        return []

    agent._tavily_search = fake_search
    results = asyncio.run(agent._search_korean_entertainment())

    assert results == []
    assert agent._core_tavily_calls <= 6
    assert agent._extra_tavily_calls <= 4
    assert agent._actual_tavily_calls <= 16
    assert agent._core_tavily_calls == 6
    # Phase 4 is intentionally reserved until ready candidates are known.
    assert agent._extra_tavily_calls == 4
    assert agent._emergency_tavily_calls == 6


def test_supplemental_queries_are_site_restricted():
    agent = object.__new__(TopicAgent)
    agent.get_config = lambda key, default=None: default
    tiers = agent._build_tiered_search_queries()

    assert tiers["supplemental"]
    assert all(query["site"] for query in tiers["supplemental"])
    assert all(query["query"].startswith("site:") for query in tiers["supplemental"])
    assert all(
        agent._is_allowed_source_url(f"https://{query['site']}/article/test")
        for query in tiers["supplemental"]
    )
    assert not agent._is_allowed_source_url("https://www.cbsnews.com/fashion-week")
    assert not agent._is_allowed_source_url("https://www.tmz.com/relationship-rumor")


def test_search_tiers_put_korean_media_before_english_sites():
    agent = object.__new__(TopicAgent)
    agent.get_config = lambda key, default=None: default
    tiers = agent._build_tiered_search_queries()

    assert len(tiers["primary"]) == 3
    assert all(query["source_language"] == "ko" for query in tiers["primary"])
    assert all(
        query["site"] in {
            "entertain.naver.com", "osen.co.kr", "newsen.com",
            "starnewskorea.com", "xportsnews.com", "mydaily.co.kr",
        }
        for query in tiers["primary"]
    )
    assert all(
        query["source_language"] == "en"
        for query in tiers["supplemental"]
    )


def test_search_phases_expand_when_raw_and_quality_are_low():
    agent = object.__new__(TopicAgent)
    agent.max_core_tavily_queries = 6
    agent.max_extra_tavily_queries = 4
    agent.max_total_tavily_credits = 12
    agent.min_candidates_before_extra = 3
    agent.freshness_hours = 24
    agent._actual_tavily_calls = 0
    agent._core_tavily_calls = 0
    agent._extra_tavily_calls = 0
    agent._emergency_tavily_calls = 0
    agent._cache_hits = 0
    agent._tavily_budget = {"credits": 0, "hard_stop_triggered": False}
    agent._seen_search_urls = set()
    agent.scoring = types.SimpleNamespace(
        _check_duplicate=lambda article: {"is_duplicate": False}
    )
    agent._filter_idol_centric_topics = lambda articles: articles
    agent.get_config = lambda key, default=None: "test-key"
    called_sources = []

    async def fake_search(query):
        called_sources.append(query["source_name"])
        if query["budget_purpose"] == "core":
            agent._core_tavily_calls += 1
        elif query["budget_purpose"] == "emergency":
            agent._emergency_tavily_calls += 1
        else:
            agent._extra_tavily_calls += 1
        return []

    agent._tavily_search = fake_search
    asyncio.run(agent._search_korean_entertainment())

    assert called_sources[:3] == [
        "Naver Entertainment", "OSEN", "NewsEn"
    ]
    assert called_sources[3:6] == [
        "StarNews", "XportsNews", "MyDaily"
    ]
    assert called_sources[6:8] == ["Koreaboo", "AllKpop"]
    assert agent._emergency_tavily_calls == 6


def test_phase4_uses_reserved_extra_budget_after_duplicate_ready_failure():
    agent = object.__new__(TopicAgent)
    agent.max_extra_tavily_queries = 4
    agent._extra_tavily_calls = 2
    agent._seen_search_urls = set()
    agent._phase4_queries = [
        {"query": "site:tenasia.hankyung.com 아이돌 컴백",
         "site": "tenasia.hankyung.com", "source_name": "TenAsia"},
        {"query": "site:dispatch.co.kr 아이돌 화보",
         "site": "dispatch.co.kr", "source_name": "Dispatch"},
    ]
    calls = []

    async def fake_search(query):
        calls.append(query["source_name"])
        agent._extra_tavily_calls += 1
        return [{
            "title": f"{query['source_name']} BTS comeback",
            "url": f"https://example.com/{query['source_name']}",
        }]

    agent._tavily_search = fake_search
    results = asyncio.run(agent._search_phase4_supplemental())

    assert calls == ["TenAsia", "Dispatch"]
    assert len(results) == 2
    assert agent._extra_tavily_calls == 4


def test_same_event_different_source_is_not_a_hard_duplicate(tmp_path):
    topics_dir = tmp_path / "data" / "topics"
    topics_dir.mkdir(parents=True)
    today = time.strftime("%Y-%m-%d")
    (topics_dir / f"{today}.json").write_text(
        """[{"topics": [{"title": "BTS V airport fashion",
        "url": "https://osen.co.kr/article/old"}]}]""",
        encoding="utf-8",
    )
    scoring = object.__new__(__import__(
        "src.topic.scoring", fromlist=["ScoringSystem"]
    ).ScoringSystem)
    scoring.config = {"project_root": str(tmp_path)}
    scoring.duplicate_days = 7

    related = scoring._check_duplicate({
        "title": "BTS V airport fashion",
        "url": "https://newsen.com/news/new",
    })
    exact = scoring._check_duplicate({
        "title": "Different title",
        "url": "https://osen.co.kr/article/old",
    })

    assert related["is_duplicate"] is False
    assert related["related_event"] is True
    assert exact["is_duplicate"] is True


def test_credit_budget_and_hard_stop():
    agent = object.__new__(TopicAgent)
    agent.max_total_tavily_credits = 12
    agent.hard_stop_tavily_credits = 20
    agent.absolute_max_tavily_credits = 50
    agent._tavily_budget = {"credits": 0, "hard_stop_triggered": False}

    assert all(agent._reserve_tavily_credits(1) for _ in range(12))
    assert not agent._reserve_tavily_credits(1)
    assert agent._tavily_budget["credits"] == 12

    agent.max_total_tavily_credits = 30
    agent.hard_stop_tavily_credits = 13
    assert not agent._reserve_tavily_credits(2)
    assert agent._tavily_budget["hard_stop_triggered"] is True


def test_emergency_search_can_use_hard_stop_budget_but_not_exceed_it():
    agent = object.__new__(TopicAgent)
    agent.max_total_tavily_credits = 12
    agent.hard_stop_tavily_credits = 20
    agent.absolute_max_tavily_credits = 50
    agent._tavily_budget = {"credits": 12, "hard_stop_triggered": False}
    agent._emergency_search_active = True

    assert all(agent._reserve_tavily_credits(1) for _ in range(8))
    assert agent._tavily_budget["credits"] == 20
    assert not agent._reserve_tavily_credits(1)
    assert agent._tavily_budget["hard_stop_triggered"] is True


def test_phase5_emergency_uses_broad_whitelisted_queries():
    agent = object.__new__(TopicAgent)
    agent._extra_tavily_calls = 4
    agent._seen_search_urls = set()
    agent.freshness_hours = 24
    agent.get_config = lambda key, default=None: default
    calls = []

    async def fake_search(query):
        calls.append(query)
        agent._extra_tavily_calls += 1
        return []

    agent._tavily_search = fake_search
    asyncio.run(agent._search_phase5_emergency())

    assert len(calls) == 6
    assert all(query["budget_purpose"] == "emergency" for query in calls)
    assert {query["site"] for query in calls} == {
        "koreaboo.com", "allkpop.com", "soompi.com"
    }


class _ImageSearchResponse:
    def raise_for_status(self):
        return None

    def json(self):
        return {"images": ["https://example.com/idol.jpg"]}


class _ImageSearchHttp:
    async def post(self, *args, **kwargs):
        return _ImageSearchResponse()


def test_image_fallback_respects_two_query_limit():
    agent = object.__new__(ImageAgent)
    agent.get_config = lambda key, default=None: default

    async def get_http():
        return _ImageSearchHttp()

    agent._get_http = get_http
    budget = {
        "credits": 0,
        "calls": 0,
        "image_calls": 0,
        "max_image_calls": 2,
        "max_total_credits": 12,
        "hard_stop_credits": 20,
        "absolute_max_credits": 50,
        "hard_stop_triggered": False,
    }
    context = {"topic_info": {"title": "BTS V update"}, "tavily_budget": budget}

    asyncio.run(agent._tavily_image_fallback(context))
    asyncio.run(agent._tavily_image_fallback(context))
    third = asyncio.run(agent._tavily_image_fallback(context))

    assert third == []
    assert budget["image_calls"] == 2
    assert budget["credits"] == 2


class _FakeWriter:
    async def run(self, context):
        topic = context["selected_topics"][0]
        article = {
            "is_success": True,
            "title": topic["title"],
            "topic_info": topic,
            "tavily_images": topic.get("_tavily_images", []),
        }
        return AgentResult(
            status=AgentStatus.SUCCESS,
            agent_name="writer_agent",
            output={"written_articles": [article]},
        )


class _FailingWriterWithSafeFallback:
    async def run(self, context):
        return AgentResult(
            status=AgentStatus.FAILED,
            agent_name="writer_agent",
            error="generation failed",
        )

    def build_safe_fallback_article(self, topic):
        return {
            "is_success": True,
            "title": "安全整理稿",
            "content_text": "安全事实整理。" * 30,
            "topic_info": topic,
            "tavily_images": topic.get("_tavily_images", []),
            "production_fallback": True,
        }


class _FakeImage:
    def __init__(self, successful_title=None):
        self.successful_title = successful_title

    async def run(self, context):
        title = context["topic_info"]["title"]
        if title != self.successful_title:
            return AgentResult(
                status=AgentStatus.FAILED,
                agent_name="image_agent",
                error="403",
            )
        return AgentResult(
            status=AgentStatus.SUCCESS,
            agent_name="image_agent",
            output={"images": [{"url": "ok", "path": "ok.jpg"}]},
        )


def test_first_image_failure_tries_second_candidate():
    WeChatMPOrchestrator = _orchestrator_class()
    orchestrator = object.__new__(WeChatMPOrchestrator)
    orchestrator.writer = _FakeWriter()
    orchestrator.image_agent = _FakeImage(successful_title="second")
    candidates = [{"title": "first"}, {"title": "second"}]

    written, images, _ = asyncio.run(
        orchestrator._write_with_image_fallback(
            {}, candidates, max_attempts=5, target_count=1
        )
    )

    assert [article["title"] for article in written] == ["second"]
    assert images
    assert candidates[0]["image_failed"] is True


def test_all_image_failures_return_no_articles():
    WeChatMPOrchestrator = _orchestrator_class()
    orchestrator = object.__new__(WeChatMPOrchestrator)
    orchestrator.writer = _FakeWriter()
    orchestrator.image_agent = _FakeImage()

    written, images, _ = asyncio.run(
        orchestrator._write_with_image_fallback(
            {}, [{"title": "first"}, {"title": "second"}],
            max_attempts=2,
            target_count=1,
        )
    )

    assert written == []
    assert images == []


def test_all_normal_writes_failed_safe_fallback_enters_image_stage():
    WeChatMPOrchestrator = _orchestrator_class()
    orchestrator = object.__new__(WeChatMPOrchestrator)
    orchestrator.writer = _FailingWriterWithSafeFallback()
    orchestrator.image_agent = _FakeImage(successful_title="candidate")
    candidate = {
        "title": "candidate",
        "_tavily_images": [{"url": "one"}, {"url": "two"}],
    }

    written, images, _ = asyncio.run(
        orchestrator._write_with_image_fallback(
            {}, [candidate], max_attempts=1, target_count=1
        )
    )

    assert written
    assert written[0]["production_fallback"] is True
    assert images
    assert orchestrator._last_candidate_attempts[-1]["image_status"] == "success"


def test_failure_outcome_distinguishes_writing_from_image_failures():
    WeChatMPOrchestrator = _orchestrator_class()
    status, message = WeChatMPOrchestrator._fallback_failure_outcome(
        {"writing": 4, "image": 0}, 4
    )
    assert status == "failed_at_writing"
    assert "写作均失败" in message
    assert "图片" not in message

    status, message = WeChatMPOrchestrator._fallback_failure_outcome(
        {"writing": 0, "image": 4}, 4
    )
    assert status == "failed_at_image"
    assert "图片均失败" in message


def test_report_aggregation_marks_partial_writing_then_failed_image():
    WeChatMPOrchestrator = _orchestrator_class()
    orchestrator = object.__new__(WeChatMPOrchestrator)
    orchestrator._last_candidate_attempts = [
        {
            "topic_title": "first", "write_status": "failed",
            "image_status": "not_run", "failure_reason": "write error",
        },
        {
            "topic_title": "second", "write_status": "success",
            "image_status": "failed", "failure_reason": "403",
        },
    ]
    aggregated = orchestrator._aggregate_fallback_results()
    assert aggregated[0].agent_name == "writer_agent"
    assert aggregated[0].status == AgentStatus.PARTIAL
    assert aggregated[1].agent_name == "image_agent"
    assert aggregated[1].status == AgentStatus.FAILED
    status, _ = orchestrator._fallback_failure_outcome(
        {"writing": 1, "image": 1}, 2
    )
    assert status == "failed_at_image"


def test_report_aggregation_all_writing_failed_has_no_image_failure():
    WeChatMPOrchestrator = _orchestrator_class()
    orchestrator = object.__new__(WeChatMPOrchestrator)
    orchestrator._last_candidate_attempts = [
        {
            "topic_title": "first", "write_status": "failed",
            "image_status": "not_run", "failure_reason": "write error",
        },
    ]
    aggregated = orchestrator._aggregate_fallback_results()
    assert len(aggregated) == 1
    assert aggregated[0].status == AgentStatus.FAILED
    status, _ = orchestrator._fallback_failure_outcome(
        {"writing": 1, "image": 0}, 1
    )
    assert status == "failed_at_writing"


def test_two_image_failures_continue_to_third_candidate():
    WeChatMPOrchestrator = _orchestrator_class()
    orchestrator = object.__new__(WeChatMPOrchestrator)
    orchestrator.writer = _FakeWriter()
    orchestrator.image_agent = _FakeImage(successful_title="third")
    candidates = [
        {"title": "first"}, {"title": "second"}, {"title": "third"},
    ]

    written, images, _ = asyncio.run(
        orchestrator._write_with_image_fallback(
            {}, candidates, max_attempts=3, target_count=1
        )
    )

    assert [article["title"] for article in written] == ["third"]
    assert images
    assert candidates[0]["image_failed"] is True
    assert candidates[1]["image_failed"] is True
