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
    agent._cache_hits = 0
    agent.max_total_tavily_credits = 12
    agent._tavily_budget = {"credits": 0, "hard_stop_triggered": False}
    agent.get_config = lambda key, default=None: "test-key"

    async def fake_search(query):
        agent._actual_tavily_calls += 1
        if query["budget_purpose"] == "core":
            agent._core_tavily_calls += 1
        else:
            agent._extra_tavily_calls += 1
        return []

    agent._tavily_search = fake_search
    results = asyncio.run(agent._search_korean_entertainment())

    assert results == []
    assert agent._core_tavily_calls <= 6
    assert agent._extra_tavily_calls <= 4
    assert agent._actual_tavily_calls <= 10


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

    assert len(tiers["primary"]) == 6
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
