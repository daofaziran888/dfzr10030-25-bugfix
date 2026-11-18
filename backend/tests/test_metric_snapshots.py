"""采集批次指标快照的边界测试：可复现、同批次唯一、空态与缺失不当通过。"""
from __future__ import annotations

import unittest

from app.services.metrics import metric_snapshots
from app.services.monitor import MonitorService
from app.services.report import ReportService


class MetricSnapshotTests(unittest.TestCase):
    def setUp(self) -> None:
        metric_snapshots.reset()
        self.monitor = MonitorService()
        self.report = ReportService()

    def test_repeated_reads_are_identical(self) -> None:
        self.assertEqual(self.monitor.list_results(), self.monitor.list_results())
        self.assertEqual(self.report.list_results(), self.report.list_results())
        self.assertEqual(self.monitor.summary_metrics(), self.monitor.summary_metrics())

    def test_detail_matches_list_without_missing_fields(self) -> None:
        listed = {row["批次号"]: row for row in self.monitor.list_results()}
        detail = self.monitor.get_entry(3)
        self.assertEqual(detail["成功率"], listed["BATCH-MONI-0003"]["成功率"])
        self.assertEqual(detail["平均延迟"], listed["BATCH-MONI-0003"]["平均延迟"])
        self.assertEqual(len(detail["事件明细"]), detail["事件总数"])
        for field in ("面板编号", "批次号", "成功率", "平均延迟", "依据指纹", "面板版本"):
            self.assertIn(field, detail)

    def test_late_event_lands_in_original_window_and_keeps_old_basis(self) -> None:
        before = metric_snapshots.snapshot("BATCH-MONI-0002")
        result = metric_snapshots.submit_event("BATCH-MONI-0002", {
            "事件编号": "EVT-LATE-0001", "结果": "成功", "延迟毫秒": 500,
            "事件时间": "2026-09-02T23:30:00", "接收时间": "2026-09-05T00:05:00",
        })
        self.assertTrue(result["ok"], result["message"])
        after = result["snapshot"]
        self.assertEqual(after["快照版本"], before["快照版本"] + 1)
        self.assertEqual(after["历史依据"][0]["成功率"], before["成功率"])
        self.assertEqual(after["成功量"], before["成功量"] + 1)

    def test_event_outside_window_is_rejected(self) -> None:
        result = metric_snapshots.submit_event("BATCH-MONI-0002", {
            "事件编号": "EVT-LATE-0002", "结果": "成功", "延迟毫秒": 100,
            "事件时间": "2026-09-04T00:10:00",
        })
        self.assertFalse(result["ok"])
        self.assertFalse(result["conflict"])
        self.assertIn("窗口", result["message"])

    def test_same_batch_concurrent_submissions_only_one_lands(self) -> None:
        result = metric_snapshots.submit_many("BATCH-MONI-0001", [
            {"event": {"事件编号": "EVT-A", "结果": "失败", "延迟毫秒": 400,
                       "事件时间": "2026-09-01T21:00:00"}, "expected_version": 1},
            {"event": {"事件编号": "EVT-B", "结果": "失败", "延迟毫秒": 410,
                       "事件时间": "2026-09-01T21:10:00"}, "expected_version": 1},
        ])
        self.assertEqual(result["applied"], 1)
        self.assertEqual(result["rejected"], 1)

    def test_empty_batch_is_not_a_pass(self) -> None:
        snapshot = metric_snapshots.snapshot("BATCH-MONI-0004")
        self.assertEqual(snapshot["指标状态"], "空态")
        self.assertIsNone(snapshot["成功率"])
        self.assertFalse(snapshot["是否通过"])

    def test_missing_event_fields_hold_back_full_snapshot(self) -> None:
        metric_snapshots._events["BATCH-MONI-0001"].append({
            "事件编号": "EVT-MISS", "结果": "成功", "延迟毫秒": None,
            "事件时间": "2026-09-01T22:00:00", "接收时间": "2026-09-01T22:00:05",
        })
        snapshot = metric_snapshots.recompute("BATCH-MONI-0001", "缺字段测试")
        self.assertEqual(snapshot["指标状态"], "待补数")
        self.assertIsNone(snapshot["成功率"])
        self.assertFalse(snapshot["是否通过"])
        self.assertTrue(snapshot["缺失事件"])

    def test_report_and_monitor_share_one_snapshot(self) -> None:
        result = self.report.regenerate("BATCH-MONI-0003", expected_version=1)
        self.assertTrue(result["ok"], result["message"])
        monitor_snapshot = self.monitor.get_batch("BATCH-MONI-0003")
        report_entry = self.report.get_entry(3)
        self.assertEqual(monitor_snapshot["成功率"], report_entry["成功率"])
        self.assertEqual(monitor_snapshot["修订序号"], report_entry["修订序号"])

    def test_second_regenerate_at_same_version_is_rejected(self) -> None:
        first = self.report.regenerate("BATCH-MONI-0003", expected_version=1)
        second = self.report.regenerate("BATCH-MONI-0003", expected_version=1)
        self.assertTrue(first["ok"])
        self.assertFalse(second["ok"])
        self.assertTrue(second["conflict"])


if __name__ == "__main__":
    unittest.main()
