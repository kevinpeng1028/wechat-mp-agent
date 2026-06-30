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
    agent.max_tavily_queries = 10
    agent.freshness_hours = 24
    agent.max_extra_tavily_queries = 4
    agent.min_candidates_before_extra = 3
    agent._actual_tavily_calls = 0
    agent._cache_hits = 0
    agent.get_config = lambda key, default=None: "test-key"

    async def fake_search(query):
        agent._actual_tavily_calls += 1
        return []

    agent._tavily_search = fake_search
    results = asyncio.run(agent._search_korean_entertainment())

    assert results == []
    assert agent._actual_tavily_calls <= agent.max_tavily_queries


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
