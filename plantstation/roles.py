"""角色加载与权限校验。"""

from __future__ import annotations

from .models import Actor, PermissionDenied, Role
from .repository import Repository


def load_actor(repo: Repository, actor_id: str) -> Actor:
    row = repo.get("actors", actor_id)
    if row is None:
        raise PermissionDenied(f"未知经办人：{actor_id}")
    return Actor(id=row["id"], name=row["name"], role=Role(row["role"]))


def require_role(actor: Actor, role: Role) -> None:
    if actor.role is not role:
        raise PermissionDenied(
            f"{actor.name}（{actor.role.value}）无权执行该操作，需要 {role.value}"
        )
