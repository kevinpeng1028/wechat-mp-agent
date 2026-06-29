"""分析周杰伦文章模板，查找所有图片位置"""
import re
from pathlib import Path

ref_file = Path(__file__).parent / "config" / "templates" / "ref_3_周杰伦新歌上线这次真的不太一样.html"
html = ref_file.read_text(encoding="utf-8")

# 找所有 img 标签（含 data-src 或 src）
img_pattern = r'<img[^>]*(?:data-src|src)="([^"]+)"[^>]*>'
imgs = re.findall(img_pattern, html)

print(f"共找到 {len(imgs)} 张图片\n")
for i, url in enumerate(imgs):
    # 找图片在HTML中的位置
    pos = html.find(url)
    # 检查图片后面还有多少内容
    after = html[pos + len(url):]
    after_text = re.sub(r'<[^>]+>', '', after).strip()
    after_text_len = len(after_text)
    
    # 检查图片前面有多少内容
    before = html[:pos]
    before_text = re.sub(r'<[^>]+>', '', before).strip()
    before_text_len = len(before_text)
    
    label = ""
    if i == 0:
        label = " [顶部Banner]"
    if i == len(imgs) - 1 and len(imgs) > 1:
        if after_text_len < 100:
            label = " [可能是底部模板图片!]"
        else:
            label = " [最后一张内容图]"
    
    print(f"图片 {i+1}/{len(imgs)}{label}")
    print(f"  URL: {url[:100]}...")
    print(f"  前面文本: {before_text_len} 字")
    print(f"  后面文本: {after_text_len} 字")
    if after_text_len < 100 and after_text:
        print(f"  后面内容: {after_text[:100]}")
    print()
