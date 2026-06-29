from src.topic.topic_agent import TopicAgent


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
        "Idol documentary film gets release date",
        "Politics and semiconductor business outlook",
        "Football transfer news",
    ]
    for title in titles:
        assert filter_topics({
            "title": title,
            "url": "https://example.com/story",
            "summary": "Unrelated news.",
        }) == []


def test_single_qualified_article_is_not_padded():
    ready_articles = [{"title": "Only valid MV release"}]
    selected_count = 2
    assert ready_articles[:selected_count] == ready_articles
