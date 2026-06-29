"""获取微信草稿箱文章，查找含底部图片的模板"""
import asyncio
import json
import re
import httpx
from pathlib import Path

async def main():
    # 加载配置
    config_path = Path(__file__).parent / "config" / "config.yaml"
    env_path = Path(__file__).parent / ".env"

    # 读取 .env
    env = {}
    if env_path.exists():
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip()

    # 读取 config.yaml 并替换变量
    with open(config_path, "r", encoding="utf-8") as f:
        import yaml
        config = yaml.safe_load(f)

    def resolve(val):
        if isinstance(val, str) and "${" in val:
            for k, v in env.items():
                val = val.replace(f"${{{k}}}", v)
        return val

    def deep_resolve(d):
        if isinstance(d, dict):
            return {k: deep_resolve(v) for k, v in d.items()}
        elif isinstance(d, list):
            return [deep_resolve(x) for x in d]
        else:
            return resolve(d)

    config = deep_resolve(config)

    app_id = config["wechat_mp"]["app_id"]
    app_secret = config["wechat_mp"]["app_secret"]
    base_url = config["wechat_mp"]["base_url"]

    # 获取 access_token
    async with httpx.AsyncClient(timeout=30.0) as http:
        resp = await http.get(
            f"{base_url}/cgi-bin/token",
            params={"grant_type": "client_credential", "appid": app_id, "secret": app_secret},
        )
        token_data = resp.json()
        if "access_token" not in token_data:
            print(f"获取token失败: {token_data}")
            return
        token = token_data["access_token"]
        print(f"✅ 获取access_token成功")

        # 获取草稿列表
        resp = await http.post(
            f"{base_url}/cgi-bin/draft/batchget",
            params={"access_token": token},
            json={"offset": 0, "count": 20, "no_content": 0},
        )
        draft_data = resp.json()

        total = draft_data.get("total_count", 0)
        drafts = draft_data.get("item", [])
        print(f"\n共 {total} 个草稿, 返回 {len(drafts)} 个")

        for i, draft in enumerate(drafts):
            media_id = draft.get("media_id", "")
            articles = draft.get("content", {}).get("news_item", [])
            print(f"\n--- 草稿 {i+1}: media_id={media_id[:30]}... ---")
            for j, article in enumerate(articles):
                title = article.get("title", "?")[:50]
                content = article.get("content", "")

                # 找所有 img 标签
                imgs = re.findall(r'<img[^>]*(?:data-src|src)="([^"]+)"[^>]*>', content)
                print(f"  文章 {j+1}: '{title}' | 图片数: {len(imgs)}")

                if imgs:
                    for k, img_url in enumerate(imgs):
                        pos = "顶部" if k == 0 else ("底部" if k == len(imgs) - 1 and len(imgs) > 1 else f"第{k+1}张")
                        print(f"    [{pos}] {img_url[:80]}...")

                    # 检查最后一张图片是否在内容末尾（底部图片）
                    last_img = imgs[-1]
                    # 找到最后一张图片在content中的位置
                    last_pos = content.rfind(last_img)
                    after_img = content[last_pos + len(last_img):]
                    # 清理HTML标签后的纯文本
                    after_text = re.sub(r'<[^>]+>', '', after_img).strip()
                    if len(after_text) < 50 and len(imgs) > 1:
                        print(f"    ⭐ 底部图片检测: 最后一张图后仅有 {len(after_text)} 字文本，可能是底部模板图片!")
                        print(f"    底部图片URL: {last_img}")

asyncio.run(main())
