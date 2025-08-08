"""Command-line tools for hytek-parser.

This module provides a CLI to convert HY3 files to JSON with optional
post-processing to a flatter, more consumable structure.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import date, datetime
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Optional

from aenum import Enum as AEnum
from attrs import asdict

from hytek_parser.hy3_parser import parse_hy3


# -----------------------------
# Serialization helpers
# -----------------------------

def _value_serializer(inst: Any, field: Any, value: Any) -> Any:
    """attrs.asdict value serializer to make objects JSON-friendly.

    - datetime/date -> ISO 8601 strings
    - aenum.Enum -> the enum name (e.g., "SCY", "MALE")
    - everything else -> unchanged
    """

    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, AEnum):
        return value.name
    return value


def _drop_none(d: MutableMapping[str, Any]) -> Dict[str, Any]:
    """Return a copy without keys whose value is None or empty dict."""
    result: Dict[str, Any] = {}
    for k, v in d.items():
        if v is None:
            continue
        if isinstance(v, dict) and not v:
            # drop empty mapping
            continue
        result[k] = v
    return result


def _sorted_events(events: Mapping[str, Any]) -> List[tuple[str, Any]]:
    def _event_key(kv: tuple[str, Any]) -> tuple[int, str]:
        key, _ = kv
        try:
            return (int(key), key)
        except (TypeError, ValueError):
            return (10**9, key)

    return sorted(events.items(), key=_event_key)


def _post_process(parsed: Dict[str, Any]) -> Dict[str, Any]:
    """Reshape the raw attrs->dict output into a flatter JSON structure.

    Output layout:
    {
      "file": {...},
      "meet": {
         ...,
         "teams": [ {...} ],
         "swimmers": [ {...} ],
         "events": [ {
            ...,
            "entries": [ {
               "swimmers": [meet_id, ...],
               "seed": {...},
               "prelim"|"swimoff"|"finals": {...}
            } ]
         } ]
      }
    }
    """

    file_section = {
        "description": parsed.get("file_description"),
        "software": parsed.get("software"),
        "date_created": parsed.get("date_created"),
        "licensee": parsed.get("licensee"),
    }

    meet: Dict[str, Any] = dict(parsed.get("meet", {}))

    # Teams -> list with explicit code, drop nested swimmers reference
    teams: Mapping[str, Any] = meet.get("teams", {}) or {}
    teams_list: List[Dict[str, Any]] = []
    for code, team in teams.items():
        team_map: Dict[str, Any] = dict(team)
        team_map["code"] = code
        team_map.pop("swimmers", None)  # global swimmers list covers this
        teams_list.append(team_map)

    # Swimmers -> list with explicit meet_id
    swimmers: Mapping[str, Any] = meet.get("swimmers", {}) or {}
    swimmers_list: List[Dict[str, Any]] = []
    for meet_id, swimmer in swimmers.items():
        swimmer_map: Dict[str, Any] = dict(swimmer)
        swimmer_map["meet_id"] = int(meet_id) if isinstance(meet_id, str) and meet_id.isdigit() else meet_id
        swimmers_list.append(swimmer_map)

    # Events -> list sorted by event number
    events: Mapping[str, Any] = meet.get("events", {}) or {}
    events_list: List[Dict[str, Any]] = []
    for event_number, event in _sorted_events(events):
        e_map: Dict[str, Any] = dict(event)
        e_map["number"] = event_number

        # Entries -> reshape timing info
        reshaped_entries: List[Dict[str, Any]] = []
        for entry in e_map.get("entries", []) or []:
            swimmers_in_entry = entry.get("swimmers", []) or []
            swimmer_ids: List[Any] = []
            for s in swimmers_in_entry:
                # Each swimmer is a mapping with meet_id
                sid = s.get("meet_id")
                swimmer_ids.append(sid)

            seed = _drop_none(
                {
                    "time": entry.get("seed_time"),
                    "course": entry.get("seed_course"),
                    "converted_time": entry.get("converted_seed_time"),
                    "converted_course": entry.get("converted_seed_time_course"),
                }
            )

            def build_leg(prefix: str) -> Optional[Dict[str, Any]]:
                dq_info = entry.get(f"{prefix}_dq_info")
                dq: Optional[Dict[str, Any]]
                if dq_info:
                    dq = _drop_none(
                        {
                            "code": dq_info.get("code"),
                            "info": dq_info.get("info_str"),
                        }
                    )
                else:
                    dq = None

                leg = _drop_none(
                    {
                        "time": entry.get(f"{prefix}_time"),
                        "course": entry.get(f"{prefix}_course"),
                        "time_code": entry.get(f"{prefix}_time_code"),
                        "dq": dq,
                        "heat": entry.get(f"{prefix}_heat"),
                        "lane": entry.get(f"{prefix}_lane"),
                        "heat_place": entry.get(f"{prefix}_heat_place"),
                        "overall_place": entry.get(f"{prefix}_overall_place"),
                        "date": entry.get(f"{prefix}_date"),
                        "splits": entry.get(f"{prefix}_splits"),
                    }
                )
                return leg or None

            reshaped_entries.append(
                _drop_none(
                    {
                        "swimmers": swimmer_ids,
                        "relay": bool(entry.get("relay", False)),
                        "seed": seed or None,
                        "prelim": build_leg("prelim"),
                        "swimoff": build_leg("swimoff"),
                        "finals": build_leg("finals"),
                        "event_number": entry.get("event_number"),
                    }
                )
            )

        e_out = _drop_none(
            {
                "number": e_map.get("number"),
                "distance": e_map.get("distance"),
                "stroke": e_map.get("stroke"),
                "course": e_map.get("course"),
                "date": e_map.get("date_"),
                "fee": e_map.get("fee"),
                "gender": e_map.get("gender"),
                "gender_age": e_map.get("gender_age"),
                "age_min": e_map.get("age_min"),
                "age_max": e_map.get("age_max"),
                "open": e_map.get("open_"),
                "relay": bool(e_map.get("relay", False)),
                "relay_team_id": e_map.get("relay_team_id"),
                "relay_swim_team_code": e_map.get("relay_swim_team_code"),
                "entries": reshaped_entries,
            }
        )
        events_list.append(e_out)

    meet_out = _drop_none(
        {
            "name": meet.get("name"),
            "facility": meet.get("facility"),
            "start_date": meet.get("start_date"),
            "end_date": meet.get("end_date"),
            "altitude": meet.get("altitude"),
            "country": meet.get("country"),
            "masters": meet.get("masters"),
            "type": meet.get("type_"),
            "course": meet.get("course"),
            "teams": teams_list,
            "swimmers": swimmers_list,
            "events": events_list,
        }
    )

    return {"file": _drop_none(file_section), "meet": meet_out}


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="hy3-to-json", description="Convert Hytek HY3 files to JSON"
    )
    parser.add_argument("input", help="Path to .hy3 file")
    parser.add_argument(
        "-o",
        "--output",
        help="Path to output .json (defaults to <input>.json)",
        default=None,
    )
    parser.add_argument(
        "--default-country",
        default="USA",
        help="Default country code when not present (default: USA)",
    )
    parser.add_argument(
        "--raw",
        action="store_true",
        help="Do not post-process; emit raw structure from attrs.asdict",
    )
    parser.add_argument(
        "--indent",
        type=int,
        default=2,
        help="JSON indent level (default: 2)",
    )

    args = parser.parse_args(list(argv) if argv is not None else None)

    input_path = os.path.abspath(args.input)
    if args.output:
        output_path = os.path.abspath(args.output)
    else:
        base, _ = os.path.splitext(input_path)
        output_path = base + ".json"

    parsed = parse_hy3(input_path, default_country=args.default_country)
    data = asdict(parsed, value_serializer=_value_serializer)

    if not args.raw:
        data = _post_process(data)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=args.indent, ensure_ascii=False)

    print(output_path)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


