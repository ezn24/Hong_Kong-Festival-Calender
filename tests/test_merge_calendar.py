from __future__ import annotations

import gzip
import sys
import unittest
import zlib
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from merge_calendar import (
    add_holiday_suffix,
    decode_payload,
    merge_calendars,
    parse_event_date,
)


def calendar(*events: str) -> str:
    return "\r\n".join(
        [
            "BEGIN:VCALENDAR",
            "VERSION:2.0",
            *events,
            "END:VCALENDAR",
            "",
        ]
    )


def event(uid: str, day: str, summary: str, extra: str = "") -> str:
    lines = [
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"DTSTART;VALUE=DATE:{day}",
        f"SUMMARY:{summary}",
    ]
    if extra:
        lines.append(extra)
    lines.append("END:VEVENT")
    return "\r\n".join(lines)


class MergeCalendarTests(unittest.TestCase):
    def test_official_date_replaces_all_icloud_events_on_same_date(self) -> None:
        official = calendar(event("official-1", "20260101", "元旦"))
        icloud = calendar(
            event("icloud-1", "20260101", "元旦"),
            event("icloud-2", "20260101", "新年"),
            event("icloud-3", "20260214", "情人節", "DESCRIPTION:保持原樣"),
        )

        merged, stats = merge_calendars(official, icloud)

        self.assertIn("SUMMARY:元旦 假日", merged)
        self.assertNotIn("UID:icloud-1", merged)
        self.assertNotIn("UID:icloud-2", merged)
        self.assertIn("UID:icloud-3", merged)
        self.assertIn("DESCRIPTION:保持原樣", merged)
        self.assertEqual(stats.removed_icloud_events, 2)
        self.assertEqual(stats.output_events, 2)

    def test_multiple_official_events_on_same_date_are_retained(self) -> None:
        official = calendar(
            event("official-1", "20260102", "農曆年初一"),
            event("official-2", "20260102", "另一公眾假期"),
        )
        icloud = calendar(event("icloud-1", "20260102", "春節"))

        merged, stats = merge_calendars(official, icloud)

        self.assertIn("UID:official-1", merged)
        self.assertIn("UID:official-2", merged)
        self.assertNotIn("UID:icloud-1", merged)
        self.assertEqual(stats.output_events, 2)

    def test_existing_holiday_suffix_is_not_duplicated(self) -> None:
        lines = event("official-1", "20260101", "元旦假日").split("\r\n")
        updated = add_holiday_suffix(lines)
        self.assertIn("SUMMARY:元旦假日", updated)
        self.assertNotIn("SUMMARY:元旦假日 假日", updated)

    def test_folded_summary_is_unfolded_modified_and_refolded(self) -> None:
        lines = [
            "BEGIN:VEVENT",
            "UID:official-1",
            "DTSTART;VALUE=DATE:20260101",
            "SUMMARY:這是一個很長的香港節日名稱這是一個很長的香港節日名稱這是一個很長的香港節日名稱",
            "END:VEVENT",
        ]
        updated = add_holiday_suffix(lines)
        unfolded = ""
        for line in updated:
            if line.startswith(" "):
                unfolded += line[1:]
            elif line.startswith("SUMMARY"):
                unfolded = line
        self.assertTrue(unfolded.endswith(" 假日"))
        self.assertTrue(all(len(line.encode("utf-8")) <= 75 for line in updated))

    def test_utc_datetime_is_compared_in_hong_kong_time(self) -> None:
        lines = [
            "BEGIN:VEVENT",
            "DTSTART:20251231T180000Z",
            "SUMMARY:Test",
            "END:VEVENT",
        ]
        self.assertEqual(parse_event_date(lines), date(2026, 1, 1))

    def test_output_is_sorted_by_date(self) -> None:
        official = calendar(event("official-2", "20260102", "第二日"))
        icloud = calendar(event("icloud-1", "20260101", "第一日"))
        merged, _ = merge_calendars(official, icloud)
        self.assertLess(merged.index("UID:icloud-1"), merged.index("UID:official-2"))

    def test_gzip_payload_is_detected_without_content_encoding_header(self) -> None:
        original = calendar(event("icloud-1", "20260214", "情人節"))
        compressed = gzip.compress(original.encode("utf-8"))
        self.assertEqual(decode_payload(compressed, charset="utf-8"), original)

    def test_gzip_content_encoding_is_supported(self) -> None:
        original = calendar(event("icloud-1", "20260214", "情人節"))
        compressed = gzip.compress(original.encode("utf-8"))
        self.assertEqual(
            decode_payload(compressed, content_encoding="gzip", charset="utf-8"),
            original,
        )

    def test_deflate_content_encoding_is_supported(self) -> None:
        original = calendar(event("icloud-1", "20260214", "情人節"))
        compressed = zlib.compress(original.encode("utf-8"))
        self.assertEqual(
            decode_payload(compressed, content_encoding="deflate", charset="utf-8"),
            original,
        )

    def test_yearly_recurring_event_gets_exdates_for_official_overlaps(self) -> None:
        official = calendar(
            event("official-2025", "20250701", "香港特別行政區成立紀念日"),
            event("official-2026", "20260701", "香港特別行政區成立紀念日"),
        )
        recurring = "\r\n".join(
            [
                "BEGIN:VEVENT",
                "UID:icloud-recurring",
                "DTSTART;VALUE=DATE:20240701",
                "SUMMARY:香港特別行政區成立紀念日",
                "RRULE:FREQ=YEARLY;COUNT=5",
                "END:VEVENT",
            ]
        )
        merged, stats = merge_calendars(official, calendar(recurring))

        self.assertIn("UID:icloud-recurring", merged)
        self.assertIn("EXDATE;VALUE=DATE:20250701,20260701", merged)
        self.assertEqual(stats.removed_icloud_events, 2)

    def test_recurring_event_start_date_overlap_is_excluded_not_deleted(self) -> None:
        official = calendar(event("official-2024", "20240101", "一月一日"))
        recurring = "\r\n".join(
            [
                "BEGIN:VEVENT",
                "UID:icloud-new-year",
                "DTSTART;VALUE=DATE:20240101",
                "SUMMARY:元旦",
                "RRULE:FREQ=YEARLY;COUNT=5",
                "END:VEVENT",
            ]
        )
        merged, _ = merge_calendars(official, calendar(recurring))

        self.assertIn("UID:icloud-new-year", merged)
        self.assertIn("EXDATE;VALUE=DATE:20240101", merged)

    def test_byday_recurrence_only_excludes_actual_occurrence(self) -> None:
        official = calendar(
            event("official-match", "20250511", "測試假日"),
            event("official-nonmatch", "20260511", "另一假日"),
        )
        recurring = "\r\n".join(
            [
                "BEGIN:VEVENT",
                "UID:icloud-mothers-day",
                "DTSTART;VALUE=DATE:20240512",
                "SUMMARY:母親節",
                "RRULE:FREQ=YEARLY;COUNT=5;BYDAY=2SU;BYMONTH=5",
                "END:VEVENT",
            ]
        )
        merged, _ = merge_calendars(official, calendar(recurring))

        self.assertIn("EXDATE;VALUE=DATE:20250511", merged)
        exdate_line = merged.split("EXDATE;VALUE=DATE:", 1)[1].split("\r\n", 1)[0]
        self.assertNotIn("20260511", exdate_line)

    def test_existing_exdate_is_not_duplicated(self) -> None:
        official = calendar(
            event("official-2025", "20250701", "香港特別行政區成立紀念日"),
            event("official-2026", "20260701", "香港特別行政區成立紀念日"),
        )
        recurring = "\r\n".join(
            [
                "BEGIN:VEVENT",
                "UID:icloud-recurring",
                "DTSTART;VALUE=DATE:20240701",
                "SUMMARY:香港特別行政區成立紀念日",
                "RRULE:FREQ=YEARLY;COUNT=5",
                "EXDATE;VALUE=DATE:20250701",
                "END:VEVENT",
            ]
        )
        merged, _ = merge_calendars(official, calendar(recurring))

        self.assertEqual(merged.count("EXDATE;VALUE=DATE:20250701"), 1)
        self.assertIn("EXDATE;VALUE=DATE:20260701", merged)


if __name__ == "__main__":
    unittest.main()
