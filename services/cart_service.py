"""
购物车服务模块

职责：
    管理本地课程购物车的增删查操作。
    购物车数据存储在 SQLite 数据库中（course_enroll.db）。

核心约束：
    - 不修改 database.py 中的表结构和状态语义
    - 已选课程（is_choose="1"）禁止加入购物车
    - 与当前课表冲突（is_conflict="1"）的教学班禁止加入购物车
"""

from __future__ import annotations

from typing import Any

from database import DatabaseManager

# 全局数据库实例
db = DatabaseManager()
db.recover_interrupted_courses()


def add_course(course: Any) -> dict[str, bool | str]:
    """
    添加课程到购物车

    校验规则：
        - is_choose="1" 的课程（已被学号选中的教学班）禁止加入
        - is_conflict="1" 的课程禁止加入
        - 重复添加时更新为 PENDING 状态

    参数：
        course: Cart 对象（需有 id, type, name, is_choose 属性）

    返回：
        {"success": bool, "message": str}
    """
    if getattr(course, "is_choose", "") == "1":
        return {"success": False, "message": "该课程已选，无法加入购物车"}
    if getattr(course, "is_conflict", "") == "1":
        return {"success": False, "message": "该教学班与已选课程冲突，无法加入购物车"}

    if not all(
        isinstance(getattr(course, field, None), str) and getattr(course, field).strip()
        for field in ("id", "type", "name")
    ):
        return {"success": False, "message": "课程信息不完整，无法加入购物车"}

    if db.add_course(course):
        return {"success": True, "message": "成功加入购物车"}
    return {"success": False, "message": "加入购物车失败"}


def delete_course(course_id: str) -> dict[str, bool | str]:
    """
    从购物车删除课程

    参数：
        course_id: 教学班 ID

    返回：
        {"success": bool, "message": str}
    """
    course_id = str(course_id or "").strip()
    if course_id and db.delete_course(course_id):
        return {"success": True, "message": "删除成功"}
    return {"success": False, "message": "删除失败或ID不存在"}


def get_courses_by_status(status: str) -> list[dict]:
    """
    按状态查询购物车课程

    参数：
        status: 课程状态（PENDING/ENROLLING/SUCCESS/FAILED，空字符串查全部）

    返回：
        课程字典列表
    """
    return db.get_courses_by_status(status)


def get_all_sorted() -> list[dict]:
    """按创建时间排序获取所有购物车课程"""
    return db.get_all_courses_sorted_by_time()


def update_status(course_id: str, status: str) -> bool:
    """更新课程状态"""
    return db.update_course_status(course_id, status)
