"""个人版身份上下文。远程访问启用前替换为真正的认证依赖。"""
from uuid import UUID

LOCAL_USER_ID = UUID("00000000-0000-0000-0000-000000000001")


def get_current_user_id() -> UUID:
    return LOCAL_USER_ID
