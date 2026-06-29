#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
微信公众号全自动运营 Agent - 入口文件

使用方式:
  # 单次运行（测试用）
  python main.py --once
  
  # 启动定时调度服务
  python main.py --schedule
  
  # 手动交互式运行（每步确认）
  python main.py --manual
  
  # 仅生成复盘报告
  python main.py --review daily|weekly|monthly
  
  # 查看配置
  python main.py --config-show
"""

import asyncio
import argparse
import sys


def parse_args():
    parser = argparse.ArgumentParser(
        description="微信公众号全自动运营 Agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python main.py --once          单次运行完整流程
  python main.py --schedule       启动定时调度
  python main.py --manual         手动交互模式
  python main.py --review daily   生成日报
  python main.py --config-show    查看当前配置
""",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--once", action="store_true", help="单次运行完整流程（非交互）")
    group.add_argument("--schedule", action="store_true", help="启动定时调度服务")
    group.add_argument("--manual", action="store_true", help="手动交互模式（每步确认）")
    group.add_argument("--review", choices=["daily", "weekly", "monthly"], help="生成复盘报告")
    group.add_argument("--config-show", action="store_true", help="查看当前配置")
    return parser.parse_args()


async def main():
    args = parse_args()

    # 确保项目根目录在 Python path 中
    project_root = __file__ and str(__file__).rsplit("/", 1)[0] or "."
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    if args.config_show:
        from src.config import Config
        cfg = Config()
        print("=" * 50)
        print(f"配置文件: {cfg.project_root / 'config' / 'config.yaml'}")
        print("=" * 50)
        print(f"LLM 提供商: {cfg.get('llm.provider')}")
        print(f"LLM 模型:   {cfg.get('llm.model')}")
        print(f"定时调度:   {cfg.get('trigger.schedule.cron')}")
        print(f"自动发布:   {cfg.get('wechat_mp.publish.auto_publish')}")
        print(f"热点检测:   {cfg.get('trigger.hot_event.enabled')}")
        print("=" * 50)
        return

    if args.once:
        from src.orchestrator import WeChatMPOrchestrator
        orch = WeChatMPOrchestrator()
        result = await orch.run_full_pipeline(manual_mode=False)
        print(f"\n运行{'成功' if result['final_status'] == 'success' else '结束'}: {result['run_id']}")
        return

    if args.manual:
        from src.orchestrator import WeChatMPOrchestrator
        orch = WeChatMPOrchestrator()
        result = await orch.run_full_pipeline(manual_mode=True)
        return

    if args.schedule:
        from src.orchestrator import run_scheduled
        await run_scheduled()
        return

    if args.review:
        from src.orchestrator import WeChatMPOrchestrator
        from src.feedback.feedback_agent import FeedbackAgent
        from src.config import Config
        
        cfg = Config()
        fb = FeedbackAgent(cfg.data)
        
        ctx = {f"{args.review}_summary": True}
        result = await fb.execute(ctx)
        
        import json
        print(json.dumps(result.output, ensure_ascii=False, indent=2))
        return


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n操作已取消。")
