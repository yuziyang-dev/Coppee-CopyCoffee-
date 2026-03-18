"""
CLI入口：提供命令行交互界面。

用法：
    # 直接传入链接
    python -m get_notes "https://v.douyin.com/xxxxx"

    # 带补充要求
    python -m get_notes "https://v.douyin.com/xxxxx" --instruction "整理营销方面的内容"

    # 交互模式
    python -m get_notes --interactive
"""

from __future__ import annotations

import argparse
import logging
import sys

from get_notes.app import GetNotesApp
from get_notes.config import AppConfig


def setup_logging(verbose: bool = False):
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def run_once(app: GetNotesApp, link: str, instruction: str | None = None):
    """处理单个链接"""
    try:
        note = app.process_link(link, instruction)
        print("\n" + app.format_note(note))
    except ValueError as e:
        print(f"\n错误: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        logging.getLogger(__name__).exception("处理失败")
        print(f"\n处理失败: {e}", file=sys.stderr)
        sys.exit(1)


def run_interactive(app: GetNotesApp):
    """交互模式：持续接受输入"""
    print("=" * 60)
    print("  Get笔记 - 链接解析与AI笔记总结")
    print("  输入链接开始，输入 q 退出")
    print("=" * 60)
    print()

    while True:
        try:
            link = input("粘贴链接 > ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\n再见！")
            break

        if not link:
            continue
        if link.lower() in ("q", "quit", "exit"):
            print("再见！")
            break

        instruction = input("补充要求（可选，按回车跳过）> ").strip() or None

        print("\n处理中...\n")
        try:
            note = app.process_link(link, instruction)
            print(app.format_note(note))
        except ValueError as e:
            print(f"错误: {e}\n")
        except Exception as e:
            logging.getLogger(__name__).exception("处理失败")
            print(f"处理失败: {e}\n")

        print()


def main():
    parser = argparse.ArgumentParser(
        prog="get_notes",
        description="Get笔记 - 通过链接解析视频/图文内容并AI生成笔记摘要",
    )
    parser.add_argument(
        "link",
        nargs="?",
        help="要解析的链接（抖音/小红书等平台分享链接）",
    )
    parser.add_argument(
        "-i", "--instruction",
        help="补充要求，如'整理营销方面的内容'",
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="进入交互模式",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="显示详细日志",
    )
    parser.add_argument(
        "--output-dir",
        help="笔记输出目录（默认 ./output）",
    )

    args = parser.parse_args()
    setup_logging(args.verbose)

    config = AppConfig()
    if args.output_dir:
        config.storage.output_dir = args.output_dir

    app = GetNotesApp(config)

    if args.interactive:
        run_interactive(app)
    elif args.link:
        run_once(app, args.link, args.instruction)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
