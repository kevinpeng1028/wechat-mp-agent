import asyncio

import numpy as np
from PIL import Image, ImageFilter

from src.image.image_agent import ImageAgent, _assess_image_quality
from src.image.image_consistency import ImageConsistencyChecker
from src.writer.writing_agent import WritingAgent


def _save_sharp_photo(path, seed=1):
    rng = np.random.default_rng(seed)
    pixels = rng.integers(0, 256, (600, 800, 3), dtype=np.uint8)
    Image.fromarray(pixels, "RGB").save(path, "JPEG", quality=92)


def _save_blurred_photo(path):
    x = np.linspace(80, 170, 800, dtype=np.uint8)
    pixels = np.tile(x, (600, 1))
    rgb = np.stack([pixels, pixels, pixels], axis=2)
    Image.fromarray(rgb, "RGB").filter(
        ImageFilter.GaussianBlur(radius=35)
    ).save(path, "JPEG", quality=80)


def test_blurred_image_is_rejected_and_sharp_image_passes(tmp_path):
    sharp = tmp_path / "sharp.jpg"
    blurred = tmp_path / "blurred.jpg"
    _save_sharp_photo(sharp)
    _save_blurred_photo(blurred)

    sharp_result = _assess_image_quality(str(sharp))
    blurred_result = _assess_image_quality(str(blurred))

    assert sharp_result["passed"] is True
    assert blurred_result["passed"] is False
    assert "清晰度不足" in blurred_result["reason"]


def test_image_relevance_rejects_different_known_artist():
    result = ImageAgent._assess_image_relevance(
        {"title": "BTS Jungkook airport update"},
        {"description": "BLACKPINK Jennie at a brand event"},
    )
    assert result["passed"] is False


def test_consistency_rejects_majority_low_quality_images():
    checker = ImageConsistencyChecker({
        "topic_agent": {"image": {"min_images_required": 2}},
        "formatter_agent": {"consistency_check": {"threshold": 40}},
    })
    images = [
        {"url": "https://img.example.com/good.jpg", "quality_passed": True,
         "quality_score": 100, "description": "BTS Jungkook"},
        {"url": "https://img.example.com/blur1.jpg", "quality_passed": False,
         "quality_reason": "blur"},
        {"url": "https://img.example.com/blur2.jpg", "quality_passed": False,
         "quality_reason": "blur"},
    ]
    result = checker.check_consistency(
        {"title": "BTS Jungkook update", "content": ""},
        images,
    )
    assert result["passed"] is False
    assert any("超过一半图片不合格" in issue for issue in result["issues"])


def test_single_high_quality_image_fails_by_default():
    checker = ImageConsistencyChecker({
        "topic_agent": {"image": {
            "min_images_required": 2,
            "allow_single_high_quality_image": False,
        }},
        "formatter_agent": {"consistency_check": {"threshold": 40}},
    })
    result = checker.check_consistency(
        {"title": "BTS Jungkook update", "content": ""},
        [{
            "url": "https://img.example.com/good.jpg",
            "quality_passed": True,
            "quality_score": 100,
            "description": "BTS Jungkook news photo",
        }],
    )
    assert result["passed"] is False
    assert any("有效图片不足" in issue for issue in result["issues"])


def test_single_high_quality_image_requires_explicit_opt_in():
    checker = ImageConsistencyChecker({
        "topic_agent": {"image": {
            "min_images_required": 2,
            "allow_single_high_quality_image": True,
        }},
        "formatter_agent": {"consistency_check": {"threshold": 40}},
    })
    result = checker.check_consistency(
        {"title": "BTS Jungkook update", "content": ""},
        [{
            "url": "https://img.example.com/good.jpg",
            "quality_passed": True,
            "quality_score": 100,
            "description": "BTS Jungkook news photo",
        }],
    )
    assert result["passed"] is True
    assert any("仅1张高质量图片" in issue for issue in result["issues"])


def test_more_than_half_bad_images_make_article_image_step_fail(tmp_path):
    sharp = tmp_path / "sharp.jpg"
    blurred1 = tmp_path / "blurred1.jpg"
    blurred2 = tmp_path / "blurred2.jpg"
    _save_sharp_photo(sharp)
    _save_blurred_photo(blurred1)
    _save_blurred_photo(blurred2)

    agent = object.__new__(ImageAgent)
    agent.get_config = lambda key, default=None: {
        "topic_agent.image": {"min_images_required": 2},
        "topic_agent.image.min_images_required": 2,
    }.get(key, default)
    agent.image_dir = tmp_path
    agent._create_smart_cover = lambda path: None

    async def fetch_og(_):
        return None

    paths = iter([sharp, blurred1, blurred2])

    async def download(url, info, idx):
        path = next(paths)
        return {
            "path": str(path),
            "filename": path.name,
            "source_url": url,
            "file_size": path.stat().st_size,
            "description": "BTS Jungkook",
        }

    agent._fetch_og_image = fetch_og
    agent._download_image = download
    result = asyncio.run(agent.execute({
        "topic_info": {"title": "BTS Jungkook update"},
        "tavily_images": [
            {"url": "https://img.example.com/1.jpg"},
            {"url": "https://img.example.com/2.jpg"},
            {"url": "https://img.example.com/3.jpg"},
        ],
    }))
    assert result.is_success is False
    assert "超过一半图片质量不合格" in result.error


def _writer_for_checks():
    writer = object.__new__(WritingAgent)
    writer.config = {
        "writer_agent": {
            "banned_phrases": [
                "被指与女友调情", "真实颜值", "隐藏颜值", "借题发挥",
                "具体细节我们不再展开", "恋情实锤", "暧昧", "翻车",
                "疑似塌房",
            ]
        }
    }
    return writer


def test_writer_length_hard_limits_and_ideal_length():
    writer = _writer_for_checks()
    assert writer._strict_check({
        "title": "测试标题", "summary": "", "content_text": "中" * 199,
    })["passed"] is False
    assert writer._strict_check({
        "title": "测试标题", "summary": "", "content_text": "中" * 400,
    })["passed"] is True
    assert writer._strict_check({
        "title": "测试标题", "summary": "", "content_text": "中" * 801,
    })["passed"] is False


def test_writer_rejects_high_risk_controversy_wording():
    writer = _writer_for_checks()
    result = writer._strict_check({
        "title": "被指与女友调情",
        "summary": "",
        "content_text": "中" * 350,
    })
    assert result["passed"] is False
    assert any("禁止口吻" in issue for issue in result["issues"])


def test_retry_prompt_targets_about_400_characters():
    writer = _writer_for_checks()
    system_prompt = writer._get_system_prompt()
    assert "目标约400字" in system_prompt
    assert "理想范围350-500字" in system_prompt
