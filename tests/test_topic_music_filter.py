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
    assert not ScoringSystem._match_star("Ten", "top ten trends over ten years")
    assert ScoringSystem._match_star("Ten", "NCT Ten shares a new update")
    assert ScoringSystem._match_star("Ten", "WayV Ten airport fashion")


def test_korean_han_and_daesung_require_explicit_artist_context():
    rejected = [
        "ITZY는 역시 믿지..가오슝 아레나 찢은 월드투어 대성황",
        "지역 사업 대성공 소식",
        "한 사람 한 번 한국 한류 이야기",
        "'다양한 체험과 공연을 함께 즐기다'…'배그' 국가대항전 "
        "'PNC 2026' [덕지순례]",
        "PUBG PNC e스포츠 국가대항전 경기",
    ]
    for title in rejected:
        assert filter_topics({
            "title": title,
            "url": "https://xportsnews.com/article/story",
            "summary": "",
        }) == []

    allowed = [
        "빅뱅 대성 콘서트 개최",
        "BIGBANG Daesung solo concert",
        "스트레이키즈 한 신곡 공개",
        "Stray Kids Han Jisung live",
        "방탄소년단 진 퀴즈쇼 언급",
        "BTS Jin quiz show mention",
        "아이브 음악방송 출연",
        "IVE music show stage",
        "있지 월드투어 성황",
        "ITZY world tour concert",
        "여자아이들 신곡 컴백",
        "아이들 걸그룹 콘서트",
    ]
    for title in allowed:
        assert filter_topics({
            "title": title,
            "url": "https://osen.co.kr/article/story",
            "summary": "",
        })

    assert not ScoringSystem._match_star("대성", "월드투어 대성황")
    assert not ScoringSystem._match_star("한", "한 사람 한 번")
    assert ScoringSystem._match_star("대성", "빅뱅 대성 콘서트")
    assert ScoringSystem._match_star("한", "스트레이키즈 한 신곡")


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


def test_macro_industry_topics_are_filtered_even_with_idol_names():
    titles = [
        "BTS and BLACKPINK lead a new K-pop generation shift",
        "Big 4 successor list includes aespa and IVE",
        "K-pop industry analysis: company landscape outlook",
        "四大公司接班人名单：BTS与aespa之后是谁",
    ]
    for title in titles:
        assert filter_topics({
            "title": title,
            "url": "https://example.com/analysis",
            "summary": "A broad industry trend article.",
        }) == []


def test_single_qualified_article_is_not_padded():
    ready_articles = [{"title": "Only valid MV release"}]
    selected_count = 2
    assert ready_articles[:selected_count] == ready_articles
