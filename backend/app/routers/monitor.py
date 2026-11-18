"""采集监控 分析看板接口：批次快照、汇总指标、参数运行与结果导出。"""
from __future__ import annotations

import csv
import io
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.schemas import ActionPayload, ActionResult, EntryPayload
from app.services.metrics import metric_snapshots
from app.services.monitor import LIST_FIELDS, STATUSES, MonitorService

router = APIRouter(prefix="/api/monitor", tags=["采集监控"])
service = MonitorService()


class BatchEventPayload(BaseModel):
    """提交一条迟到采集事件；expected_version 用于同批次并发写入的乐观校验。"""

    event: dict[str, Any] = Field(default_factory=dict)
    expected_version: int | None = None
    reason: str | None = None


class BatchEventListPayload(BaseModel):
    """同时提交多份修改；同一批次只允许第一份落库。"""

    submissions: list[dict[str, Any]] = Field(default_factory=list)
    reason: str | None = None


def csv_cell(value: Any) -> str:
    text = str(value) if value is not None else ""
    if text and text[0] in "=+-@":
        text = "'" + text
    return '"' + text.replace('"', '""') + '"'


@router.get("/summary")
def get_summary() -> dict[str, Any]:
    return {"metrics": service.summary_metrics()}


@router.get("/batches")
def list_batches() -> dict[str, Any]:
    return {"items": service.list_batches()}


@router.get("/batches/{batch_id}")
def get_batch(batch_id: str) -> dict[str, Any]:
    snapshot = service.get_batch(batch_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail=f"采集批次 {batch_id} 不存在")
    return snapshot


@router.post("/batches/{batch_id}/recompute")
def recompute_batch(batch_id: str, payload: BatchEventPayload | None = None) -> dict[str, Any]:
    reason = (payload.reason if payload else None) or "手动重算"
    snapshot = service.recompute(batch_id, reason)
    if snapshot is None:
        raise HTTPException(status_code=404, detail=f"采集批次 {batch_id} 不存在")
    return {"ok": True, "message": f"批次 {batch_id} 已按{snapshot.get('归口口径')}口径重算", "snapshot": snapshot}


@router.post("/batches/{batch_id}/events")
def submit_event(batch_id: str, payload: BatchEventPayload) -> dict[str, Any]:
    result = metric_snapshots.submit_event(
        batch_id,
        payload.event,
        expected_version=payload.expected_version,
        reason=payload.reason or "迟到事件修订",
    )
    return result


@router.post("/batches/{batch_id}/events/batch")
def submit_events(batch_id: str, payload: BatchEventListPayload) -> dict[str, Any]:
    return metric_snapshots.submit_many(
        batch_id, payload.submissions, reason=payload.reason or "迟到事件修订")


@router.get("")
def list_results() -> dict[str, Any]:
    items = service.list_results()
    return {"items": items, "total": len(items)}


@router.post("/run")
def run_analysis(payload: EntryPayload) -> dict[str, Any]:
    results = service.run_analysis(payload.values)
    return {"items": results}


@router.get("/export")
def export_entries() -> StreamingResponse:
    items = service.list_results()
    buf = io.StringIO(newline="")
    writer = csv.writer(buf)
    all_keys = ["id", *LIST_FIELDS, "批次号", "指标状态", "快照版本", "依据指纹", "是否通过"]
    writer.writerow(all_keys)
    for item in items:
        writer.writerow([csv_cell(item.get(k, "")) for k in all_keys])
    buf.seek(0)
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": "attachment; filename=monitor.csv"})


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: ActionPayload) -> ActionResult:
    """监控面板当前只开放「查看详情」，动作读取的是批次快照而不是随机指标。"""
    if payload.action.strip() != "查看详情":
        return ActionResult(ok=False, message=f"动作「{payload.action}」不属于采集监控可执行范围")
    entry = service.view_detail(entry_id)
    if entry is None:
        return ActionResult(ok=False, message=f"监控面板 {entry_id} 不存在")
    return ActionResult(ok=True, message="已按采集批次读取指标快照", entry=entry)


@router.get("/{entry_id}")
def get_entry(entry_id: int) -> dict:
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"监控面板 {entry_id} 不存在")
    return entry
