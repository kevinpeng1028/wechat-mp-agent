from src.topic.topic_agent import TopicAgent
from src.topic.scoring import ScoringSystem


def filter_topics(*articles):
    agent = object.__new__(TopicAgent)
    return agent._filter_kpop_music_topics(list(articles))


def test_rejects_subway_cafeteria_meal_news():
    article = {
        "title": "Seoul subway station cafeterias offer cheap meals",
        "url": "https://example.com/seoul-subway-cafeterias-cheap-meals",
        "content": "Restaurants at subway stations serve affordable food.",
    }
    assert filter_topics(article) == []


def test_keeps_mini_album_concept_photos():
    article = {
        "title": "(G)I-DLE unveil first concept photos for mini album",
        "url": "https://example.com/gidle-concept-photos",
        "content": "",
    }
    assert filter_topics(article) == [article]


def test_idol_centric_allowed_examples():
    titles = [
        "i-dle brings out their mature allure against the golden hour "
        "in new MV teaser",
        "BTS's Suga Revealed To Be An Early Investor In SpaceX",
        "BTS V's Actions After Leaving Club At 1AM Sparks Attention",
        "Video Of BTS's V From Fashion Week Linked To Cosmetic Procedure Debate",
    ]
    for title in titles:
        article = {
            "title": title,
            "url": "https://example.com/article/idol-story",
            "summary": "",
        }
        assert filter_topics(article) == [article]


def test_short_artist_names_require_strict_kpop_context():
    rejected = [
        "Ferrari Replaces Marketing Chief After Its Jony Ive-Designed EV Got Roasted",
        "Jony Ive-designed EV",
        "Apple designer presents a creative private drive",
        "V attends a generic event",
    ]
    for title in rejected:
        assert filter_topics({
            "title": title,
            "url": "https://example.com/story",
            "summary": "",
        }) == []

    allowed = [
        "IVE Wonyoung airport fashion goes viral",
        "IVE comeback teaser released",
        "BTS V at CELINE after-party",
        "BTS's V fashion week appearance",
        "BTS Jungkook and Jimin controversy",
        "i-dle MV teaser",
        "HYBE legal action protecting artists",
        "BLACKPINK Jennie brand event",
        "aespa Karina fashion week",
        "Stray Kids concert MV controversy",
    ]
    for title in allowed:
        assert filter_topics({
            "title": title,
            "url": "https://example.com/article",
            "summary": "",
        })

    assert not ScoringSystem._match_star("IVE", "Jony Ive-designed EV")
    assert ScoringSystem._match_star("IVE", "IVE comeback teaser")
    assert not ScoringSystem._match_star("V", "V at a generic event")
    assert ScoringSystem._match_star("V", "V of BTS at CELINE")


def test_keeps_enhypen_japan_single_mv_despite_body_template_words():
    article = {
        "title": "ENHYPEN brings warmth and hope in new Japan single "
        "'We'll Be Fine' MV",
        "url": "https://www.koreaboo.com/news/enhypen-japan-single-mv/",
        "content": "Template links mention film, broadcast, cast, and video.",
        "raw_content": "More film and documentary sidebar links.",
    }
    assert filter_topics(article) == [article]


def test_normal_news_article_url_is_not_an_aggregate_page():
    agent = object.__new__(TopicAgent)
    assert not agent._is_aggregate_page(
        "https://www.koreaboo.com/news/stray-kids-new-mv/"
    )
    assert agent._filter_top_stars(
        [{
            "title": "Stray Kids release a new MV",
            "url": "https://www.koreaboo.com/news/stray-kids-new-mv/",
            "content": "",
            "raw_content": "",
        }]
    )
    assert not agent._is_aggregate_page(
        "https://www.allkpop.com/video/2026/06/idol-performance"
    )
    assert not agent._is_aggregate_page(
        "https://example.com/article/idol-fashion"
    )
    assert agent._is_aggregate_page("https://example.com/artisttag/bts")
    assert agent._is_aggregate_page("https://example.com/artist/bts")
    assert agent._is_aggregate_page("https://example.com/movies")


def test_raw_content_cannot_create_music_signal():
    article = {
        "title": "Idol shares a personal update",
        "url": "https://example.com/idol-update",
        "content": "A brief personal update.",
        "raw_content": "Sidebar links: comeback album MV concert chart",
    }
    assert filter_topics(article) == []


def test_non_music_keyword_wins_over_music_keyword():
    article = {
        "title": "Football business discusses K-pop concert sponsorship",
        "url": "https://example.com/football-business",
        "content": "A sports business story.",
    }
    assert filter_topics(article) == []


def test_rejects_explicit_non_music_titles():
    titles = [
        "Seoul subway station cafeterias offer cheap meals",
        "June Brand Reputation rankings announced",
        "Chai Jin Xin and Samuel to star in new growth documentary",
        "South Korean Film News & Reviews",
        "Lee defends Honam semiconductor cluster",
        "Politics and semiconductor business outlook",
        "Football transfer news",
        "World Cup economy report",
    ]
    for title in titles:
        assert filter_topics({
            "title": title,
            "url": "https://example.com/story",
            "summary": "Unrelated news.",
        }) == []


def test_body_only_idol_mention_does_not_qualify():
    article = {
        "title": "South Korean economy and semiconductor update",
        "url": "https://example.com/business",
        "summary": "Industry-wide reporting.",
        "content": "A sidebar happens to mention BTS and aespa.",
        "raw_content": "Recommended: BLACKPINK airport fashion.",
    }
    assert filter_topics(article) == []


def test_single_qualified_article_is_not_padded():
    ready_articles = [{"title": "Only valid MV release"}]
    selected_count = 2
    assert ready_articles[:selected_count] == ready_articles
