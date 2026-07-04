from __future__ import annotations

import argparse
import calendar
import gzip
import os
import re
import tempfile
import time
import urllib.request
import zlib
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

OFFICIAL_URL = "https://www.1823.gov.hk/common/ical/gc/tc.ics"
ICLOUD_URL = "https://calendars.icloud.com/holiday/HK_zh.ics"
DEFAULT_OUTPUT = "hong-kong-calendar.ics"
HOLIDAY_SUFFIX = " 假日"
HONG_KONG_TZ = ZoneInfo("Asia/Hong_Kong")


@dataclass(frozen=True)
class EventRecord:
    lines: list[str]
    start_date: date | None
    source_priority: int
    source_index: int


@dataclass(frozen=True)
class MergeStats:
    official_events: int
    icloud_events: int
    removed_icloud_events: int
    output_events: int


def normalize_lines(text: str) -> list[str]:
    return text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n").split("\n")


def unfold_groups(lines: list[str]) -> Iterable[tuple[int, int, str]]:
    index = 0
    while index < len(lines):
        start = index
        logical = lines[index]
        index += 1
        while index < len(lines) and lines[index].startswith((" ", "\t")):
            logical += lines[index][1:]
            index += 1
        yield start, index, logical


def property_name(logical_line: str) -> str:
    head = logical_line.split(":", 1)[0]
    return head.split(";", 1)[0].upper()


def property_value(logical_line: str) -> str | None:
    if ":" not in logical_line:
        return None
    return logical_line.split(":", 1)[1]


def extract_components(lines: list[str], component_name: str) -> list[list[str]]:
    target = component_name.upper()
    components: list[list[str]] = []
    current: list[str] | None = None
    depth = 0

    for line in lines:
        marker = line.strip().upper()
        if marker == f"BEGIN:{target}" and current is None:
            current = [line]
            depth = 1
            continue

        if current is None:
            continue

        current.append(line)
        if marker.startswith("BEGIN:"):
            depth += 1
        elif marker.startswith("END:"):
            depth -= 1
            if depth == 0:
                components.append(current)
                current = None

    if current is not None:
        raise ValueError(f"Unclosed {component_name} component")

    return components


def validate_calendar(text: str, source_name: str) -> list[str]:
    lines = normalize_lines(text)
    markers = {line.strip().upper() for line in lines}
    if "BEGIN:VCALENDAR" not in markers or "END:VCALENDAR" not in markers:
        raise ValueError(f"{source_name} is not a valid VCALENDAR")
    if "BEGIN:VEVENT" not in markers:
        raise ValueError(f"{source_name} contains no VEVENT")
    return lines


def parse_tzid(logical_line: str) -> str | None:
    head = logical_line.split(":", 1)[0]
    match = re.search(r'(?:^|;)TZID=(?:"([^"]+)"|([^;:]+))', head, re.IGNORECASE)
    if not match:
        return None
    return match.group(1) or match.group(2)


def parse_event_date(event_lines: list[str]) -> date | None:
    for _, _, logical in unfold_groups(event_lines):
        if property_name(logical) != "DTSTART":
            continue

        value = property_value(logical)
        if value is None:
            return None
        value = value.strip()

        if re.fullmatch(r"\d{8}", value):
            return datetime.strptime(value, "%Y%m%d").date()

        if re.fullmatch(r"\d{8}T\d{6}Z", value):
            utc_time = datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
            return utc_time.astimezone(HONG_KONG_TZ).date()

        if re.fullmatch(r"\d{8}T\d{6}", value):
            local_time = datetime.strptime(value, "%Y%m%dT%H%M%S")
            tzid = parse_tzid(logical)
            if not tzid:
                return local_time.date()
            try:
                source_tz = ZoneInfo(tzid)
            except ZoneInfoNotFoundError:
                return local_time.date()
            return local_time.replace(tzinfo=source_tz).astimezone(HONG_KONG_TZ).date()

        raise ValueError(f"Unsupported DTSTART value: {value}")

    return None


def fold_content_line(line: str, limit: int = 75) -> list[str]:
    if len(line.encode("utf-8")) <= limit:
        return [line]

    folded: list[str] = []
    remaining = line
    first = True

    while remaining:
        prefix = "" if first else " "
        available = limit - len(prefix.encode("utf-8"))
        chunk_chars: list[str] = []
        chunk_bytes = 0

        for char in remaining:
            char_bytes = len(char.encode("utf-8"))
            if chunk_chars and chunk_bytes + char_bytes > available:
                break
            if not chunk_chars and char_bytes > available:
                raise ValueError("Unable to fold content line")
            chunk_chars.append(char)
            chunk_bytes += char_bytes

        chunk = "".join(chunk_chars)
        folded.append(prefix + chunk)
        remaining = remaining[len(chunk):]
        first = False

    return folded


def add_holiday_suffix(event_lines: list[str]) -> list[str]:
    updated = list(event_lines)

    for start, end, logical in unfold_groups(updated):
        if property_name(logical) != "SUMMARY":
            continue

        head, value = logical.split(":", 1)
        if value.rstrip().endswith("假日"):
            return updated
        replacement = fold_content_line(f"{head}:{value}{HOLIDAY_SUFFIX}")
        return updated[:start] + replacement + updated[end:]

    for index, line in enumerate(updated):
        if line.strip().upper() == "END:VEVENT":
            return updated[:index] + [f"SUMMARY:{HOLIDAY_SUFFIX.strip()}"] + updated[index:]

    raise ValueError("VEVENT has no END:VEVENT")


def parse_rrule(event_lines: list[str]) -> dict[str, str] | None:
    for _, _, logical in unfold_groups(event_lines):
        if property_name(logical) != "RRULE":
            continue
        value = property_value(logical)
        if not value:
            return None
        rule: dict[str, str] = {}
        for part in value.split(";"):
            if "=" not in part:
                continue
            key, item = part.split("=", 1)
            rule[key.upper()] = item.upper()
        return rule
    return None


def parse_until_date(value: str) -> date:
    if re.fullmatch(r"\d{8}", value):
        return datetime.strptime(value, "%Y%m%d").date()
    if re.fullmatch(r"\d{8}T\d{6}Z", value):
        instant = datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        return instant.astimezone(HONG_KONG_TZ).date()
    if re.fullmatch(r"\d{8}T\d{6}", value):
        return datetime.strptime(value, "%Y%m%dT%H%M%S").date()
    raise ValueError(f"Unsupported RRULE UNTIL value: {value}")


def parse_int_list(value: str | None) -> list[int]:
    if not value:
        return []
    return [int(item) for item in value.split(",") if item]


def weekday_number(code: str) -> int:
    weekdays = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}
    if code not in weekdays:
        raise ValueError(f"Unsupported BYDAY weekday: {code}")
    return weekdays[code]


def dates_for_year(start: date, rule: dict[str, str], year: int) -> list[date]:
    months = parse_int_list(rule.get("BYMONTH")) or [start.month]
    month_days = parse_int_list(rule.get("BYMONTHDAY"))
    bydays = [item for item in rule.get("BYDAY", "").split(",") if item]
    results: set[date] = set()

    for month in months:
        last_day = calendar.monthrange(year, month)[1]
        if month_days:
            for raw_day in month_days:
                day = raw_day if raw_day > 0 else last_day + raw_day + 1
                if 1 <= day <= last_day:
                    results.add(date(year, month, day))
            continue

        if bydays:
            for token in bydays:
                match = re.fullmatch(r"([+-]?\d+)?([A-Z]{2})", token)
                if not match:
                    raise ValueError(f"Unsupported BYDAY value: {token}")
                ordinal_text, weekday_code = match.groups()
                weekday = weekday_number(weekday_code)
                matching_days = [
                    day
                    for day in range(1, last_day + 1)
                    if date(year, month, day).weekday() == weekday
                ]
                if ordinal_text:
                    ordinal = int(ordinal_text)
                    index = ordinal - 1 if ordinal > 0 else ordinal
                    if -len(matching_days) <= index < len(matching_days):
                        results.add(date(year, month, matching_days[index]))
                else:
                    results.update(date(year, month, day) for day in matching_days)
            continue

        if start.day <= last_day:
            results.add(date(year, month, start.day))

    return sorted(results)


def recurrence_dates_through(event_lines: list[str], end_year: int) -> set[date]:
    start = parse_event_date(event_lines)
    rule = parse_rrule(event_lines)
    if start is None or rule is None:
        return set()
    if rule.get("FREQ") != "YEARLY":
        return set()

    interval = int(rule.get("INTERVAL", "1"))
    count = int(rule["COUNT"]) if "COUNT" in rule else None
    until = parse_until_date(rule["UNTIL"]) if "UNTIL" in rule else None
    occurrences: list[date] = []

    for year in range(start.year, end_year + 1):
        if (year - start.year) % interval != 0:
            continue
        for occurrence in dates_for_year(start, rule, year):
            if occurrence < start:
                continue
            if until is not None and occurrence > until:
                continue
            occurrences.append(occurrence)

    occurrences.sort()
    if count is not None:
        occurrences = occurrences[:count]
    return set(occurrences)


def existing_exdates(event_lines: list[str]) -> set[date]:
    result: set[date] = set()
    for _, _, logical in unfold_groups(event_lines):
        if property_name(logical) != "EXDATE":
            continue
        value = property_value(logical)
        if not value:
            continue
        for item in value.split(","):
            item = item.strip()
            if re.fullmatch(r"\d{8}", item):
                result.add(datetime.strptime(item, "%Y%m%d").date())
    return result


def add_exdates(event_lines: list[str], dates: set[date]) -> list[str]:
    missing = sorted(dates - existing_exdates(event_lines))
    if not missing:
        return list(event_lines)
    value = ",".join(item.strftime("%Y%m%d") for item in missing)
    exdate_lines = fold_content_line(f"EXDATE;VALUE=DATE:{value}")
    for index, line in enumerate(event_lines):
        if line.strip().upper() == "END:VEVENT":
            return event_lines[:index] + exdate_lines + event_lines[index:]
    raise ValueError("VEVENT has no END:VEVENT")


def unique_components(*component_groups: list[list[str]]) -> list[list[str]]:
    result: list[list[str]] = []
    seen: set[str] = set()
    for group in component_groups:
        for component in group:
            key = "\n".join(component)
            if key not in seen:
                seen.add(key)
                result.append(component)
    return result


def merge_calendars(official_text: str, icloud_text: str) -> tuple[str, MergeStats]:
    official_lines = validate_calendar(official_text, "1823 calendar")
    icloud_lines = validate_calendar(icloud_text, "iCloud calendar")

    official_blocks = extract_components(official_lines, "VEVENT")
    icloud_blocks = extract_components(icloud_lines, "VEVENT")

    official_records: list[EventRecord] = []
    public_dates: set[date] = set()

    for index, block in enumerate(official_blocks):
        event_date = parse_event_date(block)
        if event_date is not None:
            public_dates.add(event_date)
        official_records.append(
            EventRecord(add_holiday_suffix(block), event_date, 0, index)
        )

    icloud_records: list[EventRecord] = []
    removed = 0
    latest_public_year = max((item.year for item in public_dates), default=date.today().year)
    for index, block in enumerate(icloud_blocks):
        event_date = parse_event_date(block)
        recurrence_dates = recurrence_dates_through(block, latest_public_year)
        if recurrence_dates:
            overlaps = recurrence_dates & public_dates
            updated_block = add_exdates(block, overlaps)
            removed += len(overlaps)
            icloud_records.append(EventRecord(updated_block, event_date, 1, index))
            continue
        if event_date is not None and event_date in public_dates:
            removed += 1
            continue
        icloud_records.append(EventRecord(block, event_date, 1, index))

    records = official_records + icloud_records
    records.sort(
        key=lambda item: (
            item.start_date is None,
            item.start_date or date.max,
            item.source_priority,
            item.source_index,
        )
    )

    timezones = unique_components(
        extract_components(official_lines, "VTIMEZONE"),
        extract_components(icloud_lines, "VTIMEZONE"),
    )

    output_lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//ezn24//Hong Kong Holiday Calendar//ZH-Hant",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:香港節日及假日日曆",
        "X-WR-TIMEZONE:Asia/Hong_Kong",
    ]

    for component in timezones:
        output_lines.extend(component)
    for record in records:
        output_lines.extend(record.lines)

    output_lines.append("END:VCALENDAR")
    output = "\r\n".join(output_lines) + "\r\n"

    stats = MergeStats(
        official_events=len(official_records),
        icloud_events=len(icloud_blocks),
        removed_icloud_events=removed,
        output_events=len(records),
    )
    return output, stats


def decode_payload(
    payload: bytes,
    content_encoding: str | None = None,
    charset: str | None = None,
) -> str:
    encodings = [
        item.strip().lower()
        for item in (content_encoding or "").split(",")
        if item.strip() and item.strip().lower() != "identity"
    ]

    for encoding in reversed(encodings):
        if encoding in {"gzip", "x-gzip"}:
            payload = gzip.decompress(payload)
        elif encoding == "deflate":
            try:
                payload = zlib.decompress(payload)
            except zlib.error:
                payload = zlib.decompress(payload, -zlib.MAX_WBITS)
        else:
            raise ValueError(f"Unsupported Content-Encoding: {encoding}")

    if payload.startswith(b"\x1f\x8b"):
        payload = gzip.decompress(payload)

    candidates: list[str] = []
    if charset:
        candidates.append(charset)
    if payload.startswith((b"\xff\xfe", b"\xfe\xff")):
        candidates.append("utf-16")
    candidates.extend(["utf-8-sig", "utf-8"])

    last_error: UnicodeDecodeError | LookupError | None = None
    for candidate in dict.fromkeys(candidates):
        try:
            return payload.decode(candidate)
        except (UnicodeDecodeError, LookupError) as error:
            last_error = error

    raise UnicodeDecodeError(
        "utf-8", payload, 0, min(len(payload), 1), "unable to decode calendar payload"
    ) from last_error


def download_text(url: str, attempts: int = 3, timeout: int = 30) -> str:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Hong-Kong-Holiday-Calendar/1.2",
            "Accept": "text/calendar,text/plain;q=0.9,*/*;q=0.1",
            "Accept-Encoding": "identity",
        },
    )
    last_error: Exception | None = None

    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = response.read()
                return decode_payload(
                    payload,
                    content_encoding=response.headers.get("Content-Encoding"),
                    charset=response.headers.get_content_charset(),
                )
        except Exception as error:
            last_error = error
            if attempt < attempts:
                time.sleep(2 ** (attempt - 1))

    raise RuntimeError(f"Failed to download {url}") from last_error


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Merge Hong Kong holiday calendars")
    parser.add_argument("--official-url", default=OFFICIAL_URL)
    parser.add_argument("--icloud-url", default=ICLOUD_URL)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    official_text = download_text(args.official_url)
    icloud_text = download_text(args.icloud_url)
    merged, stats = merge_calendars(official_text, icloud_text)
    atomic_write(Path(args.output), merged)
    print(
        "Merged calendar written to "
        f"{args.output}: {stats.output_events} events "
        f"({stats.official_events} official, "
        f"{stats.icloud_events - stats.removed_icloud_events} iCloud retained, "
        f"{stats.removed_icloud_events} iCloud occurrences suppressed)"
    )


if __name__ == "__main__":
    main()
