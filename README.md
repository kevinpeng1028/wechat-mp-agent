# 🤖 微信公众号全自动运营 Agent

> WeChat-MP-Agent v1.0 | 从选题、写作、配图、排版到发布、复盘的全链路自动化运营系统

## 📋 架构概览

```
  ┌──────────────┐
  │ ⏰ 触发层     │  定时任务 / 手动 / 热点事件
  └──────┬───────┘
         ▼
  ┌──────────────┐
  │ ① 选题Agent  │  RSS抓取 → 关键词筛选 → LLM打分 → 选定选题
  └──────┬───────┘
         ▼
  ┌──────────────────────────┐
  │ ② 写作Agent              │
  │  ┌─ Tavily 搜索源文章 ──┐│  搜索相关文章 + 获取源文章配图
  │  │  (图文绑定核心)       ││
  │  └────────┬─────────────┘│
  │           ▼              │
  │  LLM 基于源文章改写       │  正文生成 / SEO标题 / 摘要
  └──────┬───────────────────┘
         ▼
  ┌──────────────┐
  │ ③ 配图Agent  │  下载 Tavily 源文章配图（与内容绑定）
  └──────┬───────┘
         ▼
  ┌──────────────┐
  │ ④ 排版Agent  │  MD→HTML + 样式注入 + 图片插入
  └──────┬───────┘
         ▼
  ┌──────────────┐
  │ ⑤ 发布Agent  │  素材上传 / 草稿箱 / 定时或审核发布
  └──────┬───────┘
         ▼
  ┌──────────────┐
  │ ⑥ 复盘Agent  │  数据分析 → 日报/周报 → 反馈回选题模型
  └──────────────┘
       ↑___|
    (反馈回路)
```

## 🚀 快速开始

### 1. 安装依赖

```bash
cd wechat-mp-agent
pip install -r requirements.txt
```

### 2. 配置环境变量

```bash
cp .env.example .env
# 编辑 .env，填入你的 API Key
```

**必须配置的项：**
| 变量 | 说明 |
|------|------|
| `OPENAI_API_KEY` | OpenAI API Key（用于写作/改写） |
| `TAVILY_API_KEY` | Tavily API Key（素材搜索 + 源文章配图） |
| `WECHAT_MP_APPID` | 微信公众号 AppID |
| `WECHAT_MP_APPSECRET` | 微信公众号 AppSecret |

### 3. 运行

```bash
# 单次运行完整流程（测试推荐）
python main.py --once

# 手动交互模式（每步确认）
python main.py --manual

# 启动定时调度服务（生产模式）
python main.py --schedule

# 查看当前配置
python main.py --config-show

# 生成复盘报告
python main.py --review daily
python main.py --review weekly
```

## 📁 项目结构

```
wechat-mp-agent/
├── main.py                    # 入口文件
├── requirements.txt           # Python依赖
├── config/
│   ├── config.yaml            # 主配置文件（所有参数集中管理）
│   ├── templates/             # 自定义Prompt模板目录
│   └── styles/                # 自定义排版CSS样式目录
├── src/
│   ├── __init__.py            # 包初始化
│   ├── config.py              # 配置加载器（支持环境变量替换）
│   ├── logger.py              # Loguru日志系统
│   ├── base_agent.py          # Agent基类（生命周期管理）
│   ├── orchestrator.py        # 主编排器（串联所有Agent）
│   ├── trigger/
│   │   └── scheduler.py       # ① 触发层：APScheduler定时+手动+热点
│   ├── topic/
│   │   └── topic_agent.py     # ② 选题Agent：RSS+关键词+LLM打分
│   ├── writer/
│   │   ├── writing_agent.py   # ③ 写作Agent：Tavily搜索+LLM改写/标题/摘要
│   │   └── prompt_templates/  # 可扩展的Prompt模板
│   ├── image/
│   │   └── image_agent.py     # ④ 配图Agent：Tavily源文章配图下载
│   ├── formatter/
│   │   └── formatting_agent.py# ⑤ 排版Agent：MD→HTML+样式注入
│   ├── publisher/
│   │   └── publisher_agent.py # ⑥ 发布Agent：微信MP API集成
│   └── feedback/
│       └── feedback_agent.py  # ⑦ 反馈复盘：数据采集+分析+反馈回路
├── data/
│   ├── topics/                # 选题历史记录(JSON)
│   ├── articles/              # 文章草稿(MD/HTML)
│   └── analytics/             # 运行报告和复盘数据
├── logs/                      # 运行日志
└── architecture.html          # 架构可视化页面
```

## 🔧 核心模块说明

### ① 触发层 (Trigger Layer)
- **Cron定时**: 支持 `分 时 日 月 周` 格式，默认每周一三五上午9点
- **手动触发**: `--once` 或 `--manual` 模式
- **热点检测**: 可接入微博/百度热搜API，超过热度阈值自动触发

### ② 选题 Agent
- **RSS订阅**: 内置36氪/Hacker News/少数派/虎嗅等源，可自定义扩展
- **关键词规划**: 按类别配置关注方向（如AI/产品思维/编程开发）
- **LLM多维度打分**: 相关性/时效性/独特性/可写性/受众兴趣，每项0-10分
- **权重可调**: 打分维度权重在 `config.yaml` 中自由配置

### ③ 写作 Agent
- **Tavily 搜索**: 选定选题后，用 Tavily 搜索相关源文章，获取内容素材和配图
- **图文绑定**: 图片来自 Tavily 搜索到的源文章，与文章内容天然关联
- **LLM 改写**: 基于多篇源文章内容综合改写，确保原创性
- **4种风格模板**: professional(专业)/casual(轻松)/tutorial(教程)/opinion(观点)
- **SEO标题**: 内置爆款标题策略模板（数字法/悬念法/对比法等）
- **智能摘要**: 120字以内精炼概括

### ④ 配图 Agent
- **Tavily 源图下载**: 直接使用写作 Agent 搜索到的源文章配图
- **图文绑定**: 图片与文章内容来自同一搜索结果，确保相关性
- **智能分类**: 封面图 / 文中插图 / 结尾图自动分配
- **并行下载**: 多张图片并行下载，提高效率
- **格式兼容**: 支持 jpg/png/webp/gif 等多种格式

### ⑤ 排版 Agent
- **Markdown→HTML**: 支持代码高亮/表格/引用/列表等全部特性
- **多主题样式**: modern_clean(默认)/classic/minimal/colorful
- **自动元素**: 阅读时间估算/版权声明/关注CTA引导
- **公众号兼容**: 全部内联样式，无需外部CSS/JS

### ⑥ 发布 Agent
- **Access Token 管理**: 自动获取和刷新，提前5分钟过期预警
- **批量上传图片**: 并行上传到微信素材库，自动替换HTML中的路径
- **草稿箱创建**: 支持封面图设置
- **发布模式**: 
  - `auto_publish=true`: 直接发布
  - `require_review=true`: 存草稿箱等待人工确认
  - `auto_publish=false`: 仅存草稿

### ⑦ 反馈 & 复盘
- **数据拉取**: 通过微信MP API获取阅读/点赞/分享/收藏数据
- **指标计算**: 平均阅读量/分享率/点赞率/完读率
- **阈值告警**: 低于设定值时产生告警建议
- **报告类型**: 日报小结/周报复盘/月度总结
- **反馈回路**: 基于历史表现动态调整选题评分权重（越用越聪明）

## ⚙️ 配置详解

### 修改定时任务频率
编辑 `config.yaml` 中的 `trigger.schedule.cron`：
```yaml
trigger:
  schedule:
    cron: "0 9 * * 1,3,5"  # 每周一三五早上9点
    # 每天8点: "0 8 * * *"
    # 每3小时: "0 */3 * * *"
```

### 添加RSS订阅源
```yaml
topic_agent:
  rss_feeds:
    - name: "我的信息源"
      url: "https://example.com/rss"
      category: "my_category"
```

### 调整文章风格
```yaml
writer_agent:
  default_style: "casual"  # 切换风格
  styles:
    casual:
      tone: "轻松、亲切、有共鸣"
      length_range: [1500, 2500]
```

### 开启自动发布
```yaml
wechat_mp:
  publish:
    auto_publish: true
    require_review: false  # false=直接发布不审核
```

## 🔗 API 依赖清单

| 服务 | 用途 | 是否必须 | 免费额度 |
|------|------|----------|---------|
| OpenAI API | 文本生成/改写 | ✅ 必须 | 有免费额度 |
| Tavily API | 素材搜索 + 源文章配图 | ✅ 必须 | 免费1000次/月 |
| 微信公众平台 | 发布/数据 | ✅ 必须 | 免费 |

## 🛡️ 安全注意事项

1. **`.env` 文件不要提交到版本控制**（已加入 `.gitignore` 建议）
2. **Access Token 会过期**，系统已自动处理刷新逻辑
3. **自动发布前务必先测试**：先用 `--manual` 模式走通全流程
4. **微信API有频率限制**，注意不要过于频繁调用

## 📈 后续扩展方向

- [ ] A/B 测试：同一选题生成多个标题变体
- [ ] 多账号管理：支持同时运营多个公众号
- [ ] 评论回复：自动回复评论（基于LLM）
- [ ] 用户画像分析：基于微信用户标签做更精准的选题
- [ ] Web Dashboard：可视化监控面板
- [ ] 插件化架构：允许自定义Agent扩展

## License

MIT License
