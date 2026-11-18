"""采集批次指标快照。

监控页与报告页都从同一份快照读数：成功率、平均延迟只在采集批次维度汇总一次，
重复打开页面拿到的是同一份可复现的指标，不再在读取时随机抽样。

归口口径选择「事件时间」：迟到事件只要事件时间落在原窗口内，就归入原批次并
触发一次可解释的历史修订；旧依据保留在快照的「历史依据」里，每个批次始终只有
一份当前指标快照。
"""
from __future__ import annotations

import copy
import hashlib
import json
import threading
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Iterator

from app.store import store

TERMINAL_RESULTS = ("成功", "失败", "中断", "超时")
FAILURE_RESULTS = ("失败", "中断", "超时")
RESULT_DISTRIBUTION_KEYS = ("成功", "失败", "中断", "超时")
STATUS_NORMAL = "正常"
STATUS_WARN = "预警"
STATUS_ALERT = "告警"
STATUS_EMPTY = "空态"
STATUS_PENDING = "待补数"
NORMAL_THRESHOLD = 95.0
WARN_THRESHOLD = 80.0
ATTRIBUTION_POLICY = "事件时间"

EVENT_FIELDS = [
    "事件编号", "批次号", "面板编号", "结果", "延迟毫秒", "事件时间",
    "接收时间", "恢复状态", "恢复耗时毫秒", "来源",
]

BATCH_SEEDS: list[dict[str, Any]] = [
    {
        "批次号": "BATCH-MONI-0001",
        "面板编号": "MONI-0001",
        "采集源": "新闻站点增量采集",
        "窗口开始": "2026-09-01T00:00:00",
        "窗口结束": "2026-09-01T23:59:59",
        "窗口状态": "已关闭",
        "归口口径": ATTRIBUTION_POLICY,
    },
    {
        "批次号": "BATCH-MONI-0002",
        "面板编号": "MONI-0002",
        "采集源": "商品价格API采集",
        "窗口开始": "2026-09-02T00:00:00",
        "窗口结束": "2026-09-02T23:59:59",
        "窗口状态": "已关闭",
        "归口口径": ATTRIBUTION_POLICY,
    },
    {
        "批次号": "BATCH-MONI-0003",
        "面板编号": "MONI-0003",
        "采集源": "RPA表单回填采集",
        "窗口开始": "2026-09-03T00:00:00",
        "窗口结束": "2026-09-03T23:59:59",
        "窗口状态": "已关闭",
        "归口口径": ATTRIBUTION_POLICY,
    },
    {
        "批次号": "BATCH-MONI-0004",
        "面板编号": "MONI-0004",
        "采集源": "新接入采集源（尚未上报事件）",
        "窗口开始": "2026-09-04T00:00:00",
        "窗口结束": "2026-09-04T23:59:59",
        "窗口状态": "已关闭",
        "归口口径": ATTRIBUTION_POLICY,
    },
]

# 每个批次的事件是固定的示例数据：同一份事件只会汇总出一份固定快照。
_EVENT_SEEDS: dict[str, list[tuple[str, int, str, str, str, int | None, str]]] = {
    "BATCH-MONI-0001": [
        ("成功", 218, "2026-09-01T00:12:00", "2026-09-01T00:12:04", "不适用", None, "网页采集"),
        ("成功", 265, "2026-09-01T02:25:00", "2026-09-01T02:25:03", "不适用", None, "网页采集"),
        ("成功", 312, "2026-09-01T04:40:00", "2026-09-01T04:40:05", "不适用", None, "网页采集"),
        ("成功", 198, "2026-09-01T06:55:00", "2026-09-01T06:55:02", "不适用", None, "网页采集"),
        ("成功", 421, "2026-09-01T09:10:00", "2026-09-01T09:10:06", "不适用", None, "网页采集"),
        ("成功", 287, "2026-09-01T11:25:00", "2026-09-01T11:25:04", "不适用", None, "网页采集"),
        ("成功", 356, "2026-09-01T13:40:00", "2026-09-01T13:40:05", "不适用", None, "网页采集"),
        ("成功", 244, "2026-09-01T15:55:00", "2026-09-01T15:55:03", "不适用", None, "网页采集"),
        ("成功", 301, "2026-09-01T18:10:00", "2026-09-01T18:10:04", "不适用", None, "网页采集"),
        ("成功", 232, "2026-09-01T20:25:00", "2026-09-01T20:25:03", "不适用", None, "网页采集"),
    ],
    "BATCH-MONI-0002": [
        ("成功", 402, "2026-09-02T00:20:00", "2026-09-02T00:20:05", "不适用", None, "API接入"),
        ("成功", 388, "2026-09-02T02:35:00", "2026-09-02T02:35:04", "不适用", None, "API接入"),
        ("成功", 455, "2026-09-02T04:50:00", "2026-09-02T04:50:06", "不适用", None, "API接入"),
        ("成功", 367, "2026-09-02T07:05:00", "2026-09-02T07:05:04", "不适用", None, "API接入"),
        ("成功", 501, "2026-09-02T09:20:00", "2026-09-02T09:20:07", "不适用", None, "API接入"),
        ("成功", 344, "2026-09-02T11:35:00", "2026-09-02T11:35:04", "不适用", None, "API接入"),
        ("成功", 429, "2026-09-02T13:50:00", "2026-09-02T13:50:06", "不适用", None, "API接入"),
        ("成功", 376, "2026-09-02T16:05:00", "2026-09-02T16:05:04", "不适用", None, "API接入"),
        ("成功", 468, "2026-09-02T18:20:00", "2026-09-02T18:20:06", "不适用", None, "API接入"),
        ("失败", 620, "2026-09-02T21:40:00", "2026-09-02T21:41:12", "已恢复", 3200, "API接入"),
    ],
    "BATCH-MONI-0003": [
        ("成功", 356, "2026-09-03T00:30:00", "2026-09-03T00:30:05", "不适用", None, "RPA流程"),
        ("成功", 402, "2026-09-03T02:45:00", "2026-09-03T02:45:06", "不适用", None, "RPA流程"),
        ("成功", 389, "2026-09-03T05:00:00", "2026-09-03T05:00:05", "不适用", None, "RPA流程"),
        ("成功", 475, "2026-09-03T07:15:00", "2026-09-03T07:15:07", "不适用", None, "RPA流程"),
        ("成功", 341, "2026-09-03T09:30:00", "2026-09-03T09:30:05", "不适用", None, "RPA流程"),
        ("成功", 508, "2026-09-03T11:45:00", "2026-09-03T11:45:08", "不适用", None, "RPA流程"),
        ("成功", 397, "2026-09-03T14:00:00", "2026-09-03T14:00:06", "不适用", None, "RPA流程"),
        ("失败", 880, "2026-09-03T17:20:00", "2026-09-03T17:22:01", "未恢复", None, "RPA流程"),
        ("中断", 120, "2026-09-03T19:35:00", "2026-09-03T19:35:03", "已恢复", 900, "RPA流程"),
        ("超时", 30000, "2026-09-03T22:10:00", "2026-09-03T22:10:31", "未恢复", None, "RPA流程"),
    ],
    "BATCH-MONI-0004": [],
}


def _iso_seconds(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value)).replace(microsecond=0).isoformat()
    except ValueError:
        return None


def _canonical_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered = sorted(events, key=lambda item: (str(item.get("事件时间") or ""), str(item.get("事件编号") or "")))
    return [{field: item.get(field) for field in EVENT_FIELDS} for item in ordered]


def _fingerprint(events: list[dict[str, Any]]) -> str:
    payload = json.dumps(_canonical_events(events), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


class MetricSnapshotService:
    """按采集批次汇总指标，并在进程内保存唯一一份当前快照。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.reset()

    def reset(self) -> None:
        """回到种子事件并整体重算；运维排查与测试用，重置后读数依然可复现。"""
        with self._lock:
            self._batches = {item["批次号"]: dict(item) for item in BATCH_SEEDS}
            self._events = {batch_id: [] for batch_id in self._batches}
            self._snapshots = {}
            # 修订序号在每次被接受的写入后前移，用于同批次并发提交的乐观校验；
            # 快照版本只在汇总依据变化时前移，重复读取不会制造新版本。
            self._revisions = {batch_id: 1 for batch_id in self._batches}
            self._seed_events()
            # 启动即汇总一次，让面板上的指标和快照从第一屏起就是同一份数。
            for batch_id in self.batch_ids():
                self.recompute(batch_id, "启动汇总")

    # ------------------------------------------------------------------ 锁
    @contextmanager
    def lock(self) -> Iterator[None]:
        """跨服务提交时使用同一把可重入锁，保证两边读到同一版本。"""
        with self._lock:
            yield

    # -------------------------------------------------------------- 初始化
    def _seed_events(self) -> None:
        for batch_id, specs in _EVENT_SEEDS.items():
            batch = self._batches[batch_id]
            for index, spec in enumerate(specs, start=1):
                result, latency, event_time, receive_time, recovery, recovery_ms, source = spec
                self._events[batch_id].append({
                    "事件编号": f"EVT-{batch_id.rsplit('-', 1)[-1]}-{index:03d}",
                    "批次号": batch_id,
                    "面板编号": batch["面板编号"],
                    "结果": result,
                    "延迟毫秒": latency,
                    "事件时间": event_time,
                    "接收时间": receive_time,
                    "恢复状态": recovery,
                    "恢复耗时毫秒": recovery_ms,
                    "来源": source,
                })

    # -------------------------------------------------------------- 批次查询
    def batch_ids(self) -> list[str]:
        with self._lock:
            return sorted(self._batches)

    def batch(self, batch_id: str) -> dict[str, Any] | None:
        with self._lock:
            value = self._batches.get(batch_id)
            return copy.deepcopy(value) if value else None

    def batches(self) -> list[dict[str, Any]]:
        with self._lock:
            return [copy.deepcopy(self._batches[key]) for key in sorted(self._batches)]

    def batch_for_panel(self, panel_id: int | str, panel_code: str | None = None) -> str:
        with self._lock:
            for batch in self._batches.values():
                if panel_code and batch.get("面板编号") == panel_code:
                    return str(batch["批次号"])
            for row in store.rows("monitor"):
                if str(row.get("id")) == str(panel_id) and row.get("批次号") in self._batches:
                    return str(row["批次号"])
            # 没有显式绑定批次的面板使用一个稳定的空批次，绝不拿其他批次的数据顶替。
            synthetic = f"BATCH-PANEL-{panel_id}"
            if synthetic not in self._batches:
                self._batches[synthetic] = {
                    "批次号": synthetic,
                    "面板编号": panel_code or f"PANEL-{panel_id}",
                    "采集源": str(panel_code or panel_id),
                    "窗口开始": "1970-01-01T00:00:00",
                    "窗口结束": "1970-01-01T23:59:59",
                    "窗口状态": "已关闭",
                    "归口口径": ATTRIBUTION_POLICY,
                }
                self._events[synthetic] = []
            return synthetic

    def batch_for_period(self, period: str | None) -> str | None:
        """按统计周期（日期）找到对应批次，供没有批次号的旧报告兜底使用。"""
        if not period:
            return None
        with self._lock:
            for batch in self._batches.values():
                window_start = str(batch.get("窗口开始") or "")
                if window_start[:10] == str(period)[:10]:
                    return str(batch["批次号"])
        return None

    # -------------------------------------------------------------- 快照计算
    def _compute(self, batch_id: str) -> dict[str, Any]:
        batch = self._batches[batch_id]
        events = self._events.get(batch_id, [])
        missing: list[dict[str, Any]] = []
        for event in events:
            absent = [
                field for field in ("事件编号", "结果", "延迟毫秒", "事件时间")
                if event.get(field) in (None, "")
            ]
            if event.get("结果") not in TERMINAL_RESULTS:
                absent.append("结果")
            elif not isinstance(event.get("延迟毫秒"), (int, float)) or isinstance(event.get("延迟毫秒"), bool):
                absent.append("延迟毫秒")
            elif float(event["延迟毫秒"]) < 0:
                absent.append("延迟毫秒")
            if event.get("事件时间") and _iso_seconds(event.get("事件时间")) is None:
                absent.append("事件时间")
            if absent:
                missing.append({
                    "事件编号": event.get("事件编号"),
                    "缺失字段": sorted(set(absent)),
                })

        base: dict[str, Any] = {
            "批次号": batch_id,
            "面板编号": batch.get("面板编号"),
            "采集源": batch.get("采集源"),
            "窗口开始": batch.get("窗口开始"),
            "窗口结束": batch.get("窗口结束"),
            "窗口状态": batch.get("窗口状态", "已关闭"),
            "归口口径": batch.get("归口口径", ATTRIBUTION_POLICY),
            "事件总数": len(events),
            "事件明细": copy.deepcopy(sorted(events, key=lambda item: (str(item.get("事件时间") or ""), str(item.get("事件编号") or "")))),
        }

        if not events:
            return {
                **base,
                "成功量": 0, "失败量": 0, "中断量": 0, "超时量": 0, "恢复量": 0,
                "成功率": None, "平均延迟": None, "平均延迟单位": "毫秒",
                "指标状态": STATUS_EMPTY,
                "是否可用": False,
                "是否通过": False,
                "缺失事件": [],
                "存在缺失事件": False,
                "依据指纹": _fingerprint([]),
                "当前依据": {"事件编号": [], "事件数": 0, "指纹": _fingerprint([])},
                "历史依据": [],
                "修订次数": 0,
                "统计截止": None,
                "生成时间": batch.get("窗口结束"),
                "说明": "批次无采集事件，空态不能视为通过；未生成成功率与平均延迟",
            }

        if missing:
            return {
                **base,
                "成功量": None, "失败量": None, "中断量": None, "超时量": None, "恢复量": None, "未恢复量": None,
                "恢复明细": None,
                "成功率": None, "平均延迟": None, "平均延迟单位": "毫秒",
                "指标状态": STATUS_PENDING,
                "是否可用": False,
                "是否通过": False,
                "缺失事件": missing,
                "存在缺失事件": True,
                "依据指纹": _fingerprint(events),
                "当前依据": {
                    "事件编号": sorted(str(event.get("事件编号")) for event in events),
                    "事件数": len(events),
                    "指纹": _fingerprint(events),
                },
                "历史依据": [],
                "修订次数": 0,
                "统计截止": max(str(event.get("事件时间") or "") for event in events),
                "生成时间": max(str(event.get("接收时间") or event.get("事件时间") or "") for event in events),
                "说明": "批次存在缺失字段的采集事件，先不生成完整快照，避免页面先显示一个无法复现的成功率",
            }

        terminal = [event for event in events if event.get("结果") in TERMINAL_RESULTS]
        success = sum(1 for event in terminal if event.get("结果") == "成功")
        failed = sum(1 for event in terminal if event.get("结果") == "失败")
        interrupted = sum(1 for event in terminal if event.get("结果") == "中断")
        timed_out = sum(1 for event in terminal if event.get("结果") == "超时")
        recovered = sum(1 for event in terminal if event.get("恢复状态") == "已恢复")
        unrecovered = sum(1 for event in terminal if event.get("恢复状态") == "未恢复")
        total = len(terminal)
        success_rate = round(success / total * 100, 2) if total else None
        average_latency = round(sum(float(event["延迟毫秒"]) for event in terminal) / total, 2) if total else None
        if success_rate is None:
            status = STATUS_EMPTY
        elif success_rate >= NORMAL_THRESHOLD:
            status = STATUS_NORMAL
        elif success_rate >= WARN_THRESHOLD:
            status = STATUS_WARN
        else:
            status = STATUS_ALERT
        fingerprint = _fingerprint(events)
        generated_at = max(str(event.get("接收时间") or event.get("事件时间") or "") for event in events)
        cutoff = max(str(event.get("事件时间") or "") for event in events)
        note = f"按{ATTRIBUTION_POLICY}归入批次的 {total} 条终态事件汇总"
        if status != STATUS_NORMAL:
            note += "；未达到正常阈值，需人工核对"
        return {
            **base,
            "成功量": success,
            "失败量": failed,
            "中断量": interrupted,
            "超时量": timed_out,
            "恢复量": recovered,
            "未恢复量": unrecovered,
            "结果分布": {
                "成功": success,
                "失败": failed,
                "中断": interrupted,
                "超时": timed_out,
            },
            "成功率": success_rate,
            "平均延迟": average_latency,
            "平均延迟单位": "毫秒",
            "指标状态": status,
            "是否可用": True,
            "是否通过": status == STATUS_NORMAL,
            "缺失事件": [],
            "存在缺失事件": False,
            "依据指纹": fingerprint,
            "恢复明细": {"已恢复": recovered, "未恢复": unrecovered},
            "当前依据": {
                "事件编号": sorted(str(event.get("事件编号")) for event in events),
                "事件数": len(events),
                "指纹": fingerprint,
            },
            "历史依据": [],
            "修订次数": 0,
            "修订序号": self.revision(batch_id),
            "统计截止": cutoff,
            "生成时间": generated_at,
            "说明": note,
        }

    @staticmethod
    def _basis(snapshot: dict[str, Any], reason: str) -> dict[str, Any]:
        return {
            "版本": snapshot.get("快照版本"),
            "依据指纹": snapshot.get("依据指纹"),
            "事件编号": snapshot.get("当前依据", {}).get("事件编号", []),
            "事件数": snapshot.get("当前依据", {}).get("事件数", 0),
            "成功率": snapshot.get("成功率"),
            "平均延迟": snapshot.get("平均延迟"),
            "指标状态": snapshot.get("指标状态"),
            "统计截止": snapshot.get("统计截止"),
            "修订原因": reason,
        }

    def revision(self, batch_id: str) -> int:
        with self._lock:
            return int(self._revisions.get(batch_id, 1))

    def bump_revision(self, batch_id: str) -> int:
        with self._lock:
            self._revisions[batch_id] = int(self._revisions.get(batch_id, 1)) + 1
            return self._revisions[batch_id]

    def recompute(self, batch_id: str, reason: str = "重新计算") -> dict[str, Any]:
        """重算一个批次；依据不变时返回原快照，依据变化时生成新版本并保留旧依据。"""
        with self._lock:
            if batch_id not in self._batches:
                raise KeyError(f"批次 {batch_id} 不存在")
            fresh = self._compute(batch_id)
            fresh["修订序号"] = self.revision(batch_id)
            cached = self._snapshots.get(batch_id)
            if cached is None:
                fresh["快照版本"] = 1
                fresh["修订次数"] = 0
            elif fresh.get("依据指纹") == cached.get("依据指纹") and fresh.get("指标状态") == cached.get("指标状态"):
                cached["修订序号"] = fresh["修订序号"]
                return copy.deepcopy(cached)
            else:
                fresh["快照版本"] = int(cached.get("快照版本", 1)) + 1
                fresh["修订次数"] = int(cached.get("修订次数", 0)) + 1
                history = list(cached.get("历史依据", []))
                history.append(self._basis(cached, reason))
                fresh["历史依据"] = history
            self._snapshots[batch_id] = fresh
            self._sync_panel(batch_id, fresh)
            return copy.deepcopy(fresh)

    def snapshot(self, batch_id: str) -> dict[str, Any]:
        with self._lock:
            if batch_id not in self._snapshots:
                return self.recompute(batch_id, "首次生成")
            return copy.deepcopy(self._snapshots[batch_id])

    def snapshot_for_panel(self, panel: dict[str, Any]) -> dict[str, Any]:
        batch_id = str(panel.get("批次号") or self.batch_for_panel(panel.get("id", 0), panel.get("面板编号")))
        return self.snapshot(batch_id)

    def recompute_all(self, reason: str = "重新计算") -> list[dict[str, Any]]:
        with self._lock:
            return [self.recompute(batch_id, reason) for batch_id in self.batch_ids()]

    def list_snapshots(self, *, panel_code: str | None = None, include_empty: bool = True) -> list[dict[str, Any]]:
        with self._lock:
            snapshots = [self.snapshot(batch_id) for batch_id in self.batch_ids()]
        if panel_code:
            snapshots = [snap for snap in snapshots if snap.get("面板编号") == panel_code]
        if not include_empty:
            snapshots = [snap for snap in snapshots if snap.get("指标状态") != STATUS_EMPTY]
        return snapshots

    # -------------------------------------------------------------- 迟到事件
    def _event_index(self, batch_id: str, event_id: str) -> int | None:
        for index, event in enumerate(self._events.get(batch_id, [])):
            if event.get("事件编号") == event_id:
                return index
        return None

    def _normalize_event(self, batch_id: str, raw: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        batch = self._batches[batch_id]
        event_id = str(raw.get("事件编号") or "").strip()
        if not event_id:
            event_id = f"EVT-LATE-{len(self._events.get(batch_id, [])) + 1:03d}"
        result = str(raw.get("结果") or "").strip()
        if result not in TERMINAL_RESULTS:
            return None, "结果必须是成功、失败、中断或超时"
        latency = raw.get("延迟毫秒", raw.get("延迟"))
        if isinstance(latency, bool) or not isinstance(latency, (int, float)) or float(latency) < 0:
            return None, "延迟毫秒必须是非负数字"
        event_time = _iso_seconds(raw.get("事件时间"))
        if event_time is None:
            return None, "事件时间必须是 ISO 时间"
        window_start = _iso_seconds(batch.get("窗口开始")) or ""
        window_end = _iso_seconds(batch.get("窗口结束")) or ""
        if not (window_start <= event_time <= window_end):
            return None, f"事件时间 {event_time} 不在批次 {batch_id} 的窗口 {window_start} ~ {window_end} 内"
        receive_time = _iso_seconds(raw.get("接收时间")) or event_time
        recovery = str(raw.get("恢复状态") or "不适用").strip()
        if recovery not in ("不适用", "已恢复", "未恢复"):
            return None, "恢复状态必须是不适用、已恢复或未恢复"
        recovery_ms = raw.get("恢复耗时毫秒")
        if recovery_ms is not None:
            if isinstance(recovery_ms, bool) or not isinstance(recovery_ms, (int, float)) or float(recovery_ms) < 0:
                return None, "恢复耗时毫秒必须是非负数字"
            recovery_ms = float(recovery_ms)
        return {
            "事件编号": event_id,
            "批次号": batch_id,
            "面板编号": batch.get("面板编号"),
            "结果": result,
            "延迟毫秒": float(latency),
            "事件时间": event_time,
            "接收时间": receive_time,
            "恢复状态": recovery,
            "恢复耗时毫秒": recovery_ms,
            "来源": str(raw.get("来源") or "补录"),
        }, ""

    def submit_event(
        self,
        batch_id: str,
        event: dict[str, Any],
        *,
        expected_version: int | None = None,
        reason: str = "迟到事件修订",
    ) -> dict[str, Any]:
        """提交一个事件。版本不一致时拒绝，保证同一批次一次只落一份修改。"""
        with self._lock:
            if batch_id not in self._batches:
                return {"ok": False, "conflict": False, "message": f"批次 {batch_id} 不存在", "snapshot": None}
            current = self.snapshot(batch_id)
            if expected_version is not None and int(expected_version) != int(current.get("修订序号", 1)):
                return {
                    "ok": False,
                    "conflict": True,
                    "message": f"批次已更新到修订序号 {current.get('修订序号')}，本次修改被拒绝",
                    "snapshot": current,
                }
            normalized, error = self._normalize_event(batch_id, event)
            if normalized is None:
                return {"ok": False, "conflict": False, "message": error, "snapshot": current}
            index = self._event_index(batch_id, normalized["事件编号"])
            if index is not None and self._events[batch_id][index] == normalized:
                return {
                    "ok": False,
                    "conflict": True,
                    "message": "事件已存在且内容一致，快照未变更，本次修改被拒绝",
                    "snapshot": current,
                }
            if index is None:
                self._events[batch_id].append(normalized)
            else:
                self._events[batch_id][index] = normalized
            self.bump_revision(batch_id)
            snapshot = self.recompute(batch_id, reason)
            return {"ok": True, "conflict": False, "message": "事件已归入原批次，快照已重算", "snapshot": snapshot}

    def submit_many(
        self,
        batch_id: str,
        submissions: list[dict[str, Any]],
        *,
        reason: str = "迟到事件修订",
    ) -> dict[str, Any]:
        """同一批次同时提交多份修改：只有一份落库，其余因版本冲突被拒绝。"""
        with self._lock:
            results: list[dict[str, Any]] = []
            applied = 0
            for submission in submissions:
                result = self.submit_event(
                    batch_id,
                    submission.get("event") or submission.get("事件") or submission,
                    expected_version=submission.get("expected_version", submission.get("expectedVersion")),
                    reason=reason,
                )
                if result.get("ok"):
                    applied += 1
                results.append(result)
            return {
                "ok": applied > 0,
                "applied": applied,
                "rejected": len(results) - applied,
                "results": results,
                "snapshot": self.snapshot(batch_id),
            }

    # -------------------------------------------------------------- 面板同步
    def _sync_panel(self, batch_id: str, snapshot: dict[str, Any]) -> None:
        for row in store.rows("monitor"):
            if str(row.get("批次号")) != batch_id and row.get("面板编号") != snapshot.get("面板编号"):
                continue
            row["成功率"] = snapshot.get("成功率")
            row["平均延迟"] = snapshot.get("平均延迟")
            row["今日采集量"] = snapshot.get("事件总数", 0)
            row["失败重试"] = int(snapshot.get("失败量") or 0) + int(snapshot.get("中断量") or 0) + int(snapshot.get("超时量") or 0)
            row["异常告警"] = 1 if snapshot.get("指标状态") in (STATUS_WARN, STATUS_ALERT, STATUS_EMPTY, STATUS_PENDING) else 0
            row["更新时间"] = snapshot.get("生成时间") or row.get("更新时间")
            row["批次号"] = batch_id
            row["面板版本"] = int(snapshot.get("修订序号", 1))
            row["指标状态"] = snapshot.get("指标状态")
            row["快照版本"] = snapshot.get("快照版本")

    # -------------------------------------------------------------- 汇总
    def summary_metrics(self) -> list[dict[str, Any]]:
        """监控页和报告页共用同一套汇总卡片，刷新不会随机跳变。"""
        snapshots = self.list_snapshots()
        usable = [snap for snap in snapshots if snap.get("是否可用") and snap.get("成功率") is not None]
        abnormal = [snap for snap in snapshots if snap.get("指标状态") not in (STATUS_NORMAL,)]
        if usable:
            weighted_total = sum(int(snap.get("成功量") or 0) for snap in usable)
            attempts = sum(
                int(snap.get("成功量") or 0) + int(snap.get("失败量") or 0)
                + int(snap.get("中断量") or 0) + int(snap.get("超时量") or 0)
                for snap in usable
            )
            success_ratio = round(weighted_total / attempts, 3) if attempts else 0
            weighted_latency = sum(
                float(snap.get("平均延迟") or 0) * (
                    int(snap.get("成功量") or 0) + int(snap.get("失败量") or 0)
                    + int(snap.get("中断量") or 0) + int(snap.get("超时量") or 0)
                )
                for snap in usable
            )
            average_latency = round(weighted_latency / attempts, 2) if attempts else 0
        else:
            success_ratio = 0
            average_latency = 0
        return [
            {"label": "覆盖数据源", "value": len(snapshots), "trend": 0},
            {"label": "最新指标值", "value": success_ratio, "trend": 0},
            {"label": "异常记录", "value": len(abnormal), "trend": 0},
            {"label": "平均延迟", "value": average_latency, "trend": 0},
            {"label": "空态批次", "value": sum(1 for snap in snapshots if snap.get("指标状态") == STATUS_EMPTY), "trend": 0},
        ]

    def report_summary(self, batch_id: str) -> dict[str, Any]:
        """报告服务读数：直接复用监控面板快照，不再自行抽样。"""
        snapshot = self.snapshot(batch_id)
        total = int(snapshot.get("事件总数") or 0)
        failed = int(snapshot.get("失败量") or 0) + int(snapshot.get("中断量") or 0) + int(snapshot.get("超时量") or 0)
        average_ms = snapshot.get("平均延迟")
        return {
            "批次号": snapshot.get("批次号"),
            "面板编号": snapshot.get("面板编号"),
            "采集源": snapshot.get("采集源"),
            "统计周期": f"{snapshot.get('窗口开始')} ~ {snapshot.get('窗口结束')}",
            "快照版本": snapshot.get("快照版本"),
            "修订序号": snapshot.get("修订序号"),
            "面板版本": snapshot.get("修订序号", snapshot.get("快照版本")),
            "总采集量": total,
            "成功量": int(snapshot.get("成功量") or 0),
            "失败量": failed,
            "失败明细": {
                "失败": int(snapshot.get("失败量") or 0),
                "中断": int(snapshot.get("中断量") or 0),
                "超时": int(snapshot.get("超时量") or 0),
                "已恢复": int(snapshot.get("恢复量") or 0),
                "未恢复": int(snapshot.get("未恢复量") or 0),
            },
            "成功率": snapshot.get("成功率"),
            "平均延迟": snapshot.get("平均延迟"),
            "平均延迟单位": snapshot.get("平均延迟单位", "毫秒"),
            "平均耗秒": round(float(average_ms) / 1000, 3) if average_ms is not None else None,
            "指标状态": snapshot.get("指标状态"),
            "是否可用": snapshot.get("是否可用"),
            "是否通过": snapshot.get("是否通过"),
            "依据指纹": snapshot.get("依据指纹"),
            "生成时间": snapshot.get("生成时间"),
            "统计截止": snapshot.get("统计截止"),
            "事件总数": total,
            "窗口状态": snapshot.get("窗口状态"),
            "归口口径": snapshot.get("归口口径"),
            "说明": snapshot.get("说明"),
            "快照": snapshot,
        }


metric_snapshots = MetricSnapshotService()
