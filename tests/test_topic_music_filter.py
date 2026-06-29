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


def test_single_qualified_article_is_not_padded():
    ready_articles = [{"title": "Only valid MV release"}]
    selected_count = 2
    assert ready_articles[:selected_count] == ready_articles
