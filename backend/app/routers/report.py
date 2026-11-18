"""采集报告 分析看板接口：按批次核对成功率与延迟，并维护待审清单。"""
from __future__ import annotations

import csv
import io
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.schemas import ActionPayload, ActionResult, EntryPayload
from app.services.metrics import metric_snapshots
from app.services.report import LIST_FIELDS, STATUSES, ReportService

router = APIRouter(prefix="/api/report", tags=["采集报告"])
service = ReportService()


class RegeneratePayload(BaseModel):
    expected_version: int | None = None
    reason: str | None = None


class RegenerateListPayload(BaseModel):
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


@router.get("/reviews")
def list_reviews() -> dict[str, Any]:
    items = service.review_items()
    return {"items": items, "total": len(items)}


@router.get("/batches")
def list_batches() -> dict[str, Any]:
    return {"items": metric_snapshots.list_snapshots()}


@router.post("/batches/{batch_id}/regenerate")
def regenerate(batch_id: str, payload: RegeneratePayload | None = None) -> dict[str, Any]:
    return service.regenerate(
        batch_id,
        expected_version=payload.expected_version if payload else None,
        reason=(payload.reason if payload else None) or "报告重算",
    )


@router.post("/batches/{batch_id}/regenerate/batch")
def regenerate_many(batch_id: str, payload: RegenerateListPayload) -> dict[str, Any]:
    return service.regenerate_many(
        batch_id, payload.submissions, reason=payload.reason or "报告重算")


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
                             headers={"Content-Disposition": "attachment; filename=report.csv"})


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: ActionPayload) -> ActionResult:
    """「重新生成」与监控面板共用一次快照重算，不另起抽样。"""
    if payload.action.strip() != "重新生成":
        return ActionResult(ok=False, message=f"动作「{payload.action}」不属于采集报告可执行范围")
    entry = service.get_entry(entry_id)
    if entry is None:
        return ActionResult(ok=False, message=f"采集报告 {entry_id} 不存在")
    batch_id = str(entry.get("批次号") or "")
    if not batch_id:
        return ActionResult(ok=False, message="报告尚未绑定采集批次，无法重算")
    result = service.regenerate(batch_id, reason="报告重新生成")
    updated = service.get_entry(entry_id)
    return ActionResult(ok=bool(result.get("ok")), message=str(result.get("message")), entry=updated)


@router.get("/{entry_id}")
def get_entry(entry_id: int) -> dict:
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"采集报告 {entry_id} 不存在")
    return entry
