"""采集报告 分析看板服务。

报告不在自己的读取路径上抽样：成功率与平均延迟直接读取监控面板的批次快照，
生成同一批次的摘要；重算动作会同时更新监控面板快照与报告摘要，两边看到的是
同一份快照版本。
"""
from __future__ import annotations

import threading
from typing import Any

from app.services.metrics import (
    STATUS_ALERT,
    STATUS_EMPTY,
    STATUS_PENDING,
    STATUS_WARN,
    metric_snapshots,
)
from app.store import store

MODULE = "report"
REQUIRED_FIELDS = ["报告编号", "采集源", "统计周期"]
STATUS_ORDER = ["生成中", "已生成", "已发送"]
ACTION_RULES = {"重新生成": "已生成"}
NEGATIVE_ACTIONS = []
STATUSES = ["生成中", "已生成", "已发送"]
LIST_FIELDS = ["报告编号", "采集源", "统计周期", "总采集量", "成功量", "失败量", "平均耗秒", "生成时间"]

REVIEW_STATUSES = (STATUS_WARN, STATUS_ALERT, STATUS_EMPTY, STATUS_PENDING)


class ReportService:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._reviews: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------ 报告行读数
    def _snapshot_for_row(self, row: dict[str, Any]) -> dict[str, Any]:
        batch_id = str(row.get("批次号") or "")
        if batch_id:
            try:
                return metric_snapshots.snapshot(batch_id)
            except KeyError:
                pass
        # 没有批次号的旧报告：按统计周期找原批次，找不到再用面板兜底，仍然只读一份快照。
        batch_id = (
            metric_snapshots.batch_for_period(self._period(row))
            or metric_snapshots.batch_for_panel(row.get("id", 0), str(row.get("采集源") or ""))
        )
        return metric_snapshots.snapshot(batch_id)

    @staticmethod
    def _period(row: dict[str, Any]) -> str:
        raw = str(row.get("统计周期") or "")
        if "T" in raw or " " in raw:
            return raw.split("T")[0].split(" ")[0]
        return raw

    def entry_view(self, row: dict[str, Any]) -> dict[str, Any]:
        snapshot = self._snapshot_for_row(row)
        summary = metric_snapshots.report_summary(snapshot["批次号"])
        view = dict(row)
        view.update({
            "批次号": summary["批次号"],
            "面板编号": summary["面板编号"],
            "快照版本": summary["快照版本"],
            "修订序号": summary.get("修订序号"),
            "报告版本": summary.get("修订序号", summary["快照版本"]),
            "总采集量": summary["总采集量"],
            "成功量": summary["成功量"],
            "失败量": summary["失败量"],
            "失败明细": summary["失败明细"],
            "成功率": summary["成功率"],
            "平均延迟": summary["平均延迟"],
            "平均延迟单位": summary["平均延迟单位"],
            "平均耗秒": summary["平均耗秒"],
            "指标状态": summary["指标状态"],
            "是否可用": summary["是否可用"],
            "是否通过": summary["是否通过"],
            "依据指纹": summary["依据指纹"],
            "统计截止": summary["统计截止"],
            "说明": summary["说明"],
        })
        if not summary["是否可用"]:
            view["status"] = "生成中"
        elif row.get("status") == "生成中":
            view["status"] = "已生成"
        view["pending"] = summary["指标状态"] in REVIEW_STATUSES
        view["abnormal"] = summary["指标状态"] in (STATUS_WARN, STATUS_ALERT, STATUS_EMPTY, STATUS_PENDING)
        return view

    def list_results(self) -> list[dict[str, Any]]:
        return [self.entry_view(row) for row in store.rows(MODULE)]

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        row = store.find(MODULE, entry_id)
        if row is None:
            return None
        view = self.entry_view(row)
        snapshot = self._snapshot_for_row(row)
        view["快照"] = snapshot
        view["事件明细"] = snapshot.get("事件明细", [])
        view["历史依据"] = snapshot.get("历史依据", [])
        view["修订次数"] = snapshot.get("修订次数", 0)
        view["面板版本"] = snapshot.get("快照版本")
        view["待审清单"] = [
            item for item in self.review_items() if item.get("批次号") == view.get("批次号")
        ]
        return view

    # ------------------------------------------------------------ 按批次核对
    def run_analysis(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        batch_id = str(params.get("批次号", params.get("报告编号", "")) or "").strip()
        rows = self.list_results()
        if batch_id:
            rows = [
                row for row in rows
                if row.get("批次号") == batch_id
                or row.get("报告编号") == batch_id
                or str(row.get("id")) == batch_id
            ]
        results: list[dict[str, Any]] = []
        for index, row in enumerate(rows, start=1):
            baseline = self._baseline(row.get("批次号"))
            success_rate = row.get("成功率")
            results.append({
                "id": index,
                "分组": row.get("批次号"),
                "报告编号": row.get("报告编号"),
                "采集源": row.get("采集源"),
                "统计周期": row.get("统计周期"),
                "总采集量": row.get("总采集量"),
                "成功量": row.get("成功量"),
                "失败量": row.get("失败量"),
                "成功率": success_rate,
                "平均延迟": row.get("平均延迟"),
                "平均耗秒": row.get("平均耗秒"),
                "指标状态": row.get("指标状态"),
                "是否通过": row.get("是否通过"),
                "快照版本": row.get("快照版本"),
                "依据指纹": row.get("依据指纹"),
                "指标值": round(float(success_rate) / 100, 4) if success_rate is not None else None,
                "对比基准": baseline,
                "差异": (
                    round(float(success_rate) / 100 - baseline, 4)
                    if success_rate is not None and baseline is not None else None
                ),
            })
        return results

    @staticmethod
    def _baseline(batch_id: Any) -> float | None:
        if not batch_id:
            return None
        try:
            snapshot = metric_snapshots.snapshot(str(batch_id))
        except KeyError:
            return None
        history = snapshot.get("历史依据") or []
        if history and history[-1].get("成功率") is not None:
            return round(float(history[-1]["成功率"]) / 100, 4)
        return 0.95 if snapshot.get("成功率") is not None else None

    # ------------------------------------------------------------ 待审清单
    def review_items(self) -> list[dict[str, Any]]:
        """指标不是「正常」的批次全部进入待审清单，空态和待补数不能被当成通过。

        清单从当前快照派生，重复打开报告页得到同一份清单；重算后会顺带刷新
        批次级记录，保证两处口径一致。
        """
        with self._lock:
            items: dict[str, dict[str, Any]] = {}
            for snapshot in metric_snapshots.list_snapshots():
                if snapshot.get("指标状态") not in REVIEW_STATUSES and snapshot.get("是否通过"):
                    continue
                batch_id = str(snapshot.get("批次号"))
                item = {
                    "批次号": batch_id,
                    "面板编号": snapshot.get("面板编号"),
                    "采集源": snapshot.get("采集源"),
                    "指标状态": snapshot.get("指标状态"),
                    "是否通过": bool(snapshot.get("是否通过")),
                    "成功率": snapshot.get("成功率"),
                    "平均延迟": snapshot.get("平均延迟"),
                    "平均延迟单位": snapshot.get("平均延迟单位"),
                    "快照版本": snapshot.get("快照版本"),
                    "修订序号": snapshot.get("修订序号"),
                    "依据指纹": snapshot.get("依据指纹"),
                    "缺失事件": snapshot.get("缺失事件", []),
                    "说明": snapshot.get("说明"),
                    "生成时间": snapshot.get("生成时间"),
                }
                items[batch_id] = item
            return [items[key] for key in sorted(items)]

    def _refresh_reviews(self, snapshot: dict[str, Any]) -> dict[str, Any] | None:
        batch_id = str(snapshot["批次号"])
        existing = self._reviews.get(batch_id)
        status = snapshot.get("指标状态")
        if status not in REVIEW_STATUSES and not (existing and existing.get("是否通过") is False):
            self._reviews.pop(batch_id, None)
            return None
        item = {
            "批次号": batch_id,
            "面板编号": snapshot.get("面板编号"),
            "采集源": snapshot.get("采集源"),
            "指标状态": status,
            "是否通过": bool(snapshot.get("是否通过")),
            "成功率": snapshot.get("成功率"),
            "平均延迟": snapshot.get("平均延迟"),
            "平均延迟单位": snapshot.get("平均延迟单位"),
            "快照版本": snapshot.get("快照版本"),
            "修订序号": snapshot.get("修订序号"),
            "依据指纹": snapshot.get("依据指纹"),
            "缺失事件": snapshot.get("缺失事件", []),
            "说明": snapshot.get("说明"),
            "生成时间": snapshot.get("生成时间"),
        }
        self._reviews[batch_id] = item
        return item

    # ------------------------------------------------------------ 重算动作
    def _sync_report_rows(self, snapshot: dict[str, Any]) -> list[dict[str, Any]]:
        summary = metric_snapshots.report_summary(snapshot["批次号"])
        touched: list[dict[str, Any]] = []
        for row in store.rows(MODULE):
            if row.get("批次号") != summary["批次号"] and self._period(row) != self._period({"统计周期": summary["统计周期"]}):
                continue
            if row.get("批次号") not in (None, summary["批次号"]):
                continue
            row["批次号"] = summary["批次号"]
            row["采集源"] = summary["采集源"]
            row["总采集量"] = summary["总采集量"]
            row["成功量"] = summary["成功量"]
            row["失败量"] = summary["失败量"]
            row["平均耗秒"] = summary["平均耗秒"]
            row["生成时间"] = summary["生成时间"]
            row["报告版本"] = summary.get("修订序号", summary["快照版本"])
            if summary["是否可用"]:
                row["status"] = "已生成" if row.get("status") == "生成中" else row.get("status", "已生成")
                row["pending"] = summary["指标状态"] in REVIEW_STATUSES
                row["abnormal"] = summary["指标状态"] in (STATUS_WARN, STATUS_ALERT)
            else:
                row["status"] = "生成中"
                row["pending"] = True
                row["abnormal"] = True
            touched.append(row)
        return touched

    def regenerate(
        self,
        batch_id: str,
        *,
        expected_version: int | None = None,
        reason: str = "报告重算",
    ) -> dict[str, Any]:
        """重算一个批次：监控面板快照与报告摘要同时更新，版本不符则整份拒绝。"""
        with metric_snapshots.lock():
            try:
                current = metric_snapshots.snapshot(batch_id)
            except KeyError:
                return {"ok": False, "conflict": False, "message": f"采集批次 {batch_id} 不存在", "snapshot": None}
            if expected_version is not None and int(expected_version) != int(current.get("修订序号", 1)):
                return {
                    "ok": False,
                    "conflict": True,
                    "message": f"批次已更新到修订序号 {current.get('修订序号')}，本次重算被拒绝，未写入任何一边",
                    "snapshot": current,
                }
            metric_snapshots.bump_revision(batch_id)
            snapshot = metric_snapshots.recompute(batch_id, reason)
            with self._lock:
                review = self._refresh_reviews(snapshot)
                rows = self._sync_report_rows(snapshot)
            return {
                "ok": True,
                "conflict": False,
                "message": f"批次 {batch_id} 的监控快照与报告摘要已按同一份依据更新",
                "snapshot": snapshot,
                "review": review,
                "reports": [dict(row) for row in rows],
            }

    def regenerate_many(
        self,
        batch_id: str,
        submissions: list[dict[str, Any]],
        *,
        reason: str = "报告重算",
    ) -> dict[str, Any]:
        """同一批次同时提交多份重算：只有第一份落库，其余因版本冲突被拒绝。"""
        with metric_snapshots.lock():
            results: list[dict[str, Any]] = []
            applied = 0
            for submission in submissions:
                result = self.regenerate(
                    batch_id,
                    expected_version=submission.get("expected_version", submission.get("expectedVersion")),
                    reason=str(submission.get("reason") or reason),
                )
                if result.get("ok"):
                    applied += 1
                results.append(result)
            return {
                "ok": applied > 0,
                "applied": applied,
                "rejected": len(results) - applied,
                "results": results,
                "snapshot": metric_snapshots.snapshot(batch_id),
            }

    def summary_metrics(self) -> list[dict[str, Any]]:
        return metric_snapshots.summary_metrics()
