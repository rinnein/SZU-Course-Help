"""卡密验证模块 - 统一验证入口。

本模块是卡密验证的唯一入口点，由 app.py 登录接口调用。
内部委托给 security.key_manager 执行 Ed25519 签名验证。

卡密方案：
    Ed25519 签名 + 规范化 JSON 载荷 + Base64URL 编码

验证流程：
    1. 校验 SZU3 令牌结构并严格 Base64URL 解码
    2. 用本机 Ed25519 公钥验证签名
    3. 校验规范化载荷、密钥指纹和学号绑定

卡密永久有效：
    卡密与学号一一绑定，不含到期字段；更换签名密钥会使旧卡密失效。
"""

import logging

from security.key_manager import verify_card_key as _verify

logger = logging.getLogger(__name__)


def verify_card_key(student_id: str, card_key: str) -> bool:
    """验证学号绑定；无论失败原因为何都只返回 ``False``。"""
    try:
        return _verify(student_id, card_key)
    except Exception:
        logger.exception("Card-key verification failed")
        return False
