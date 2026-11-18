"""采集监控 分析看板服务。

看板的每个面板都绑定一个采集批次，指标一律从批次快照读取：同一批次重复打开
页面拿到同一个成功率与平均延迟，不再在读取时随机抽样。
"""
from __future__ import annotations

from typing import Any

from app.services.metrics import (
    STATUS_EMPTY,
    STATUS_PENDING,
    metric_snapshots,
)
from app.store import store

MODULE = "monitor"
REQUIRED_FIELDS = ["面板编号", "采集源总数", "今日采集量"]
STATUS_ORDER = ["正常", "预警", "告警"]
ACTION_RULES = {"查看详情": "正常"}
NEGATIVE_ACTIONS = []
STATUSES = ["正常", "预警", "告警", "空态", "待补数"]
LIST_FIELDS = ["面板编号", "采集源总数", "今日采集量", "成功率", "平均延迟", "失败重试", "异常告警", "更新时间"]

SNAPSHOT_FIELDS = [
    "批次号", "成功率", "平均延迟", "平均延迟单位", "指标状态", "快照版本",
    "依据指纹", "是否可用", "是否通过", "事件总数", "成功量", "失败量",
    "中断量", "超时量", "结果分布", "恢复明细", "统计截止", "生成时间", "说明",
]


class MonitorService:
    def _row_view(self, row: dict[str, Any]) -> dict[str, Any]:
        snapshot = metric_snapshots.snapshot_for_panel(row)
        view = dict(row)
        view["批次号"] = snapshot.get("批次号")
        view["快照版本"] = snapshot.get("快照版本")
        view["面板版本"] = snapshot.get("快照版本")
        for field in SNAPSHOT_FIELDS:
            view[field] = snapshot.get(field)
        view["指标状态"] = snapshot.get("指标状态")
        view["指标值"] = (
            round(float(snapshot["成功率"]) / 100, 4)
            if snapshot.get("成功率") is not None else None
        )
        view["对比基准"] = self._baseline(snapshot)
        view["对比基准口径"] = "上一版依据" if snapshot.get("历史依据") else "正常阈值 95%"
        view["差异"] = (
            round(view["指标值"] - view["对比基准"], 4)
            if view["指标值"] is not None and view["对比基准"] is not None else None
        )
        # 面板自身的状态仍用声明过的三档；空态与待补数归入预警并在指标状态里说清楚，
        # 这样既不会显示成正常，也不会跳出页面约定的状态集合。
        indicator = snapshot.get("指标状态")
        view["status"] = indicator if indicator in ("正常", "预警", "告警") else "预警"
        view["指标状态"] = indicator
        view["pending"] = indicator in (STATUS_EMPTY, STATUS_PENDING, "预警", "告警")
        view["abnormal"] = indicator != "正常"
        return view

    @staticmethod
    def _baseline(snapshot: dict[str, Any]) -> float | None:
        history = snapshot.get("历史依据") or []
        if history:
            previous = history[-1].get("成功率")
            return round(float(previous) / 100, 4) if previous is not None else None
        return 0.95 if snapshot.get("成功率") is not None else None

    def list_results(self) -> list[dict[str, Any]]:
        """列表按面板去重，每个面板只读自己批次的同一份快照。"""
        seen: set[str] = set()
        results: list[dict[str, Any]] = []
        for row in store.rows(MODULE):
            view = self._row_view(row)
            key = str(view.get("批次号") or view.get("面板编号") or view.get("id"))
            if key in seen:
                continue
            seen.add(key)
            results.append(view)
        return results

    def list_batches(self) -> list[dict[str, Any]]:
        return metric_snapshots.list_snapshots()

    def get_batch(self, batch_id: str) -> dict[str, Any] | None:
        try:
            return metric_snapshots.snapshot(batch_id)
        except KeyError:
            return None

    def run_analysis(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        """按批次汇总的分析结果；同样的参数重复运行得到同样的结果。"""
        batch_id = str(
            params.get("批次号", params.get("面板编号", "")) or ""
        ).strip()
        snapshots = metric_snapshots.list_snapshots()
        if batch_id:
            snapshots = [
                snap for snap in snapshots
                if snap.get("批次号") == batch_id or snap.get("面板编号") == batch_id
            ]
        results: list[dict[str, Any]] = []
        for index, snapshot in enumerate(snapshots, start=1):
            baseline = self._baseline(snapshot)
            results.append({
                "id": index,
                "分组": snapshot.get("批次号"),
                "面板编号": snapshot.get("面板编号"),
                "采集源": snapshot.get("采集源"),
                "事件总数": snapshot.get("事件总数", 0),
                "成功量": snapshot.get("成功量"),
                "失败量": snapshot.get("失败量"),
                "中断量": snapshot.get("中断量"),
                "超时量": snapshot.get("超时量"),
                "恢复量": snapshot.get("恢复量"),
                "成功率": snapshot.get("成功率"),
                "平均延迟": snapshot.get("平均延迟"),
                "平均延迟单位": snapshot.get("平均延迟单位", "毫秒"),
                "指标状态": snapshot.get("指标状态"),
                "是否通过": snapshot.get("是否通过"),
                "快照版本": snapshot.get("快照版本"),
                "依据指纹": snapshot.get("依据指纹"),
                "指标值": (
                    round(float(snapshot["成功率"]) / 100, 4)
                    if snapshot.get("成功率") is not None else None
                ),
                "对比基准": baseline,
                "差异": (
                    round(float(snapshot["成功率"]) / 100 - baseline, 4)
                    if snapshot.get("成功率") is not None and baseline is not None else None
                ),
            })
        return results

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        row = store.find(MODULE, entry_id)
        if row is None:
            return None
        view = self._row_view(row)
        snapshot = metric_snapshots.snapshot_for_panel(row)
        # 详情页保留面板全部字段，并额外带出快照依据，不能在详情里丢字段。
        view["事件明细"] = snapshot.get("事件明细", [])
        view["当前依据"] = snapshot.get("当前依据")
        view["历史依据"] = snapshot.get("历史依据", [])
        view["窗口开始"] = snapshot.get("窗口开始")
        view["窗口结束"] = snapshot.get("窗口结束")
        view["窗口状态"] = snapshot.get("窗口状态")
        view["归口口径"] = snapshot.get("归口口径")
        view["修订次数"] = snapshot.get("修订次数", 0)
        return view

    def view_detail(self, entry_id: int) -> dict[str, Any] | None:
        return self.get_entry(entry_id)

    def recompute(self, batch_id: str, reason: str = "重新计算") -> dict[str, Any] | None:
        try:
            return metric_snapshots.recompute(batch_id, reason)
        except KeyError:
            return None

    def summary_metrics(self) -> list[dict[str, Any]]:
        return metric_snapshots.summary_metrics()
