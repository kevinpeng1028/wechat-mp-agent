from src.image.image_agent import _is_blocked_image, BLOCKED_KEYWORDS

test_urls = [
    "https://0.soompi.io/wp-content/uploads/2026/06/26211521/BTS-RESCENE-CORTIS-IOI.jpg",
    "https://6.soompi.io/wp-content/uploads/image/20260627041515_BTS-RESCENE-CORTIS-I.jpg",
    "https://0.soompi.io/wp-content/uploads/2026/05/31181257/soompi-june-filing-for-l.jpg",
]

for url in test_urls:
    for desc in ["", "BTS photo", "ATEEZ dance practice"]:
        result = _is_blocked_image(url, desc)
        if result:
            # 找出匹配的关键词
            text = (url + " " + desc).lower()
            matched_kw = [kw for kw in BLOCKED_KEYWORDS if kw in text]
            print(f"BLOCKED: {url[:60]}... desc='{desc}' | matched: {matched_kw}")
            break
    else:
        print(f"ALLOWED: {url[:60]}...")
