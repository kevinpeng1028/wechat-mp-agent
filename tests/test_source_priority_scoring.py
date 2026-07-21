from src.topic.scoring import ScoringSystem


def test_korean_media_concrete_event_with_images_gets_priority_bonus():
    scoring = ScoringSystem({"topic_agent": {"scoring": {}}})
    article = {
        "title": "IVE comeback teaser released",
        "content": "IVE released a comeback teaser.",
        "url": "https://osen.co.kr/article/123",
        "source_url": "https://osen.co.kr/article/123",
        "original_title": "IVE comeback teaser released",
        "published_at": "2026-07-03",
    }
    images = [{"url": "one.jpg"}, {"url": "two.jpg"}]

    priority, adjustment, notes = scoring._score_editorial_priority(
        article, images
    )

    assert priority == 10
    assert adjustment > 0
    assert "韩国本土媒体" in notes
    assert "具体艺人事件" in notes


def test_macro_analysis_is_heavily_downgraded():
    scoring = ScoringSystem({"topic_agent": {"scoring": {}}})
    article = {
        "title": "K-pop generation shift and Big 4 industry analysis",
        "content": "A broad market outlook.",
        "url": "https://osen.co.kr/article/456",
    }
    _, adjustment, notes = scoring._score_editorial_priority(
        article, [{"url": "one.jpg"}]
    )

    assert adjustment < 0
    assert "宏观分析" in notes


def test_music_and_tour_events_outrank_quiz_mentions_and_esports():
    scoring = ScoringSystem({"topic_agent": {"scoring": {}}})
    images = [{"url": "one.jpg"}, {"url": "two.jpg"}]
    music = {
        "title": "ITZY world tour concert",
        "content": "ITZY continued the tour.",
        "url": "https://osen.co.kr/article/1",
    }
    quiz = {
        "title": "BTS Jin mentioned on UK quiz show",
        "content": "A quiz referenced Jin.",
        "url": "https://osen.co.kr/article/2",
    }
    esports = {
        "title": "PUBG PNC e스포츠 국가대항전",
        "content": "게임 대회 경기",
        "url": "https://xportsnews.com/article/3",
    }
    _, music_adjustment, _ = scoring._score_editorial_priority(music, images)
    _, quiz_adjustment, _ = scoring._score_editorial_priority(quiz, images)
    _, esports_adjustment, esports_notes = scoring._score_editorial_priority(
        esports, images
    )

    assert music_adjustment > quiz_adjustment > esports_adjustment
    assert "电竞/游戏" in esports_notes
