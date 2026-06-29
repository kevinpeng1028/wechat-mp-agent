"""提取周杰伦文章模板的顶部和底部图片"""
import re
from pathlib import Path

ref_file = Path(__file__).parent / "config" / "templates" / "ref_3_周杰伦新歌上线这次真的不太一样.html"
html = ref_file.read_text(encoding="utf-8")

# 找所有 img 标签的完整信息
img_pattern = r'<img[^>]*(?:data-src|src)="([^"]+)"[^>]*/?>'
imgs = list(re.finditer(img_pattern, html))

print(f"共找到 {len(imgs)} 张图片\n")

for i, match in enumerate(imgs):
    url = match.group(1)
    full_tag = match.group(0)
    pos = match.start()
    
    # 图片后面剩余的纯文本
    after = html[match.end():]
    after_text = re.sub(r'<[^>]+>', '', after).strip()
    
    label = "顶部Banner" if i == 0 else ("底部模板" if i == len(imgs)-1 and len(after_text) < 200 else f"内容图{i}")
    
    print(f"=== 图片 {i+1}: [{label}] ===")
    print(f"URL: {url}")
    print(f"后面文本({len(after_text)}字): {after_text[:200]}")
    print(f"完整标签: {full_tag[:300]}")
    print()
