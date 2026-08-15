"""Terminal entry point for the Shenzhen University course helper.

The student number and Card Key are now entered on the Web login page itself;
the terminal only boots the local service and opens the browser.
"""

from __future__ import annotations

import sys

from logging_config import configure_logging


def print_separator(char: str = "=", width: int = 64) -> None:
    print(char * width)


def print_banner() -> None:
    print_separator()
    print("  深大抢课助手")
    print("  本地 WebUI · 手动首登 · OCR 自动重登 · Card Key V3")
    print("  作者：Weeye · Misakait")
    print_separator()


def start_course_system() -> None:
    """Start the local-only FastAPI service; the login page issues the card key."""
    import app

    print("\n正在启动本地选课界面...")
    print(f"访问地址: {app.get_login_url()}")
    print("登录时只需填写学号和密码，系统会自动为本机签发并校验 Card Key。")
    print("当前为预选阶段时，请只浏览和整理课程，不要启动抢课。")
    print("保持本终端窗口开启。\n")
    print_separator("-")
    app.start_server()


def main() -> None:
    """Boot the local Web UI; login and Card Key generation happen in the page."""
    configure_logging()
    print_banner()
    start_course_system()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n程序已停止。")
        sys.exit(0)
