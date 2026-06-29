# WeChat MP Auto-Operation Agent
"""
微信公众号全自动运营 Agent 系统

架构：
  触发层 → 选题Agent → 写作Agent ─┬→ 配图Agent ─┐
                                   │           │
                                排版Agent     │
                                   │           │
                                发布Agent ←───┘
                                   │
                              反馈&复盘 → (反馈回路回传选题)
"""

__version__ = "1.0.0"
__author__ = "WeChat-MP-Agent"
