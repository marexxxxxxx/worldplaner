"""To-Dos / Hermes-Tasks (Spezifikation D.4)."""

from fastapi import APIRouter, Depends, Query

from .. import db
from ..models import TaskCreate, TaskUpdate
from ..permissions import require_permission
from ..services import tasks as tasks_service
from .events import bus

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.get("")
async def list_tasks(
    status: str = Query(default="all", pattern="^(open|done|all)$"),
    limit: int = Query(default=50, ge=1, le=200),
    _perm: None = Depends(require_permission("tasks")),
) -> list[dict]:
    with db.get_conn() as conn:
        return tasks_service.list_tasks(conn, status, limit)


@router.post("", status_code=201)
async def create_task(payload: TaskCreate) -> dict:
    with db.get_conn() as conn:
        task = tasks_service.create_task(conn, payload)
    bus.publish("task.created", task)
    return task


@router.patch("/{task_id}")
async def update_task(
    task_id: str,
    payload: TaskUpdate,
    _perm: None = Depends(require_permission("tasks")),
) -> dict:
    with db.get_conn() as conn:
        task = tasks_service.update_task(conn, task_id, payload)
    bus.publish("task.updated", task)
    return task


@router.delete("/{task_id}", status_code=204)
async def delete_task(task_id: str) -> None:
    with db.get_conn() as conn:
        tasks_service.delete_task(conn, task_id)
    bus.publish("task.deleted", {"id": task_id})
