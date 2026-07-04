from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from merge_calendar import add_holiday_suffix, merge_calendars, parse_event_date


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


if __name__ == "__main__":
    unittest.main()
