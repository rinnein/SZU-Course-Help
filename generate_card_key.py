"""Standalone Card Key V3 generator and verifier."""

from __future__ import annotations

import re
import sys

from security.key_manager import (
    KeyManagementError,
    generate_card_key,
    get_or_create_key_pair,
    get_public_key_fingerprint,
    verify_card_key,
)

STUDENT_ID_PATTERN = re.compile(r"^\d{6,12}$")


def _safe_input(prompt: str) -> str:
    try:
        return input(prompt).strip()
    except (KeyboardInterrupt, EOFError):
        print("\n已取消。")
        raise SystemExit(0) from None


def _read_student_id() -> str:
    while True:
        student_id = _safe_input("请输入学号: ")
        if STUDENT_ID_PATTERN.fullmatch(student_id):
            return student_id
        print("学号必须是 6 至 12 位数字。")


def _read_choice() -> str:
    while True:
        choice = _safe_input("请选择操作 (1/2): ")
        if choice in {"1", "2"}:
            return choice
        print("无效的选项，请输入 1 或 2。")


def main() -> None:
    print("=" * 64)
    print("  深大选课助手 Card Key V3 工具")
    print("=" * 64)
    print("1. 生成卡密")
    print("2. 验证卡密")
    choice = _read_choice()

    if choice == "1":
        student_id = _read_student_id()
        key = get_or_create_key_pair()
        card_key = generate_card_key(student_id, key)
        print(f"\n学号: {student_id}")
        print(f"密钥指纹: {get_public_key_fingerprint()}")
        print(f"卡密: {card_key}")
        return

    if choice == "2":
        student_id = _read_student_id()
        card_key = _safe_input("请输入卡密: ")
        result = verify_card_key(student_id, card_key)
        print("\n验证通过。" if result else "\n验证失败。")


if __name__ == "__main__":
    try:
        main()
    except (KeyManagementError, OSError, ValueError) as exc:
        print(f"\n卡密操作失败: {exc}")
        sys.exit(1)
