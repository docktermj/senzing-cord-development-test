#!/usr/bin/env python3
"""Update CORD_stats.xlsx with the statistics of the given Senzing-ready JSONL files.

Only the row of each given file changes, matched on SOURCE and GEO, which come
from the file name <source>-<geo>.jsonl split on the last hyphen. Its counts are
rewritten (a feature no longer present becomes 0), LAST_UPDATED is set to
--date, and UPDATE_REASON to --reason. A file with no row gets a new one,
formatted like the last row. Every other row and cell is left as it is.

usage: get_cord_stats.py --stats CORD_stats.xlsx --reason TEXT [--date YYYY-MM-DD] FILE...
"""

import argparse
import datetime
import os
import sys
from copy import copy

from openpyxl import load_workbook

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from json2attribute import json2attribute  # noqa: E402

CONFIG = os.path.join(HERE, "sz_default_config.json")
# Columns written from the arguments, not computed from the file.
MANUAL_COLUMNS = ("LAST_UPDATED", "UPDATE_REASON")
STYLE_ATTRIBUTES = ("font", "border", "fill", "number_format", "protection", "alignment")


def source_and_geo(path):
    """Split <source>-<geo>.jsonl on the last hyphen."""
    name = os.path.basename(path)
    if not name.endswith(".jsonl") or "-" not in name[: -len(".jsonl")]:
        raise ValueError(f"{name}: expected <source>-<geo>.jsonl")
    source, geo = name[: -len(".jsonl")].rsplit("-", 1)
    if not source or not geo:
        raise ValueError(f"{name}: expected <source>-<geo>.jsonl")
    return source, geo


def file_stats(json_parser, path):
    """Return {column name: value} for one JSONL file."""
    source, geo = source_and_geo(path)
    column_values = {"SOURCE": source, "GEO": geo, "RECORD_COUNT": 0}
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            for attr_data in json_parser.parse(line):
                if not attr_data.get("FTYPE_CODE"):
                    continue
                if attr_data["FTYPE_CODE"] == "RECORD_TYPE":
                    column_name = f"{attr_data['ATTR_VALUE']}_COUNT"
                else:
                    column_name = f"{attr_data['FTYPE_CODE']}_FEATURES"
                column_values[column_name] = column_values.get(column_name, 0) + 1
            column_values["RECORD_COUNT"] += 1
            if column_values["RECORD_COUNT"] % 100000 == 0:
                print(f"{column_values['RECORD_COUNT']} rows processed for {path}")
    print(f"{column_values['RECORD_COUNT']} rows processed for {path}, complete!")
    return column_values


def find_row(ws, column_header, source, geo):
    source_col = column_header.index("SOURCE") + 1
    geo_col = column_header.index("GEO") + 1
    for row_idx in range(2, ws.max_row + 1):
        if (ws.cell(row=row_idx, column=source_col).value == source
                and ws.cell(row=row_idx, column=geo_col).value == geo):
            return row_idx
    return None


def append_row(ws, column_header):
    """Add a row after the last one, copying its formatting, with every value 0."""
    last_idx = ws.max_row
    row_idx = last_idx + 1
    for col in range(1, len(column_header) + 1):
        last = ws.cell(row=last_idx, column=col)
        cell = ws.cell(row=row_idx, column=col)
        if last.has_style:
            for attribute in STYLE_ATTRIBUTES:
                setattr(cell, attribute, copy(getattr(last, attribute)))
        cell.value = 0
    return row_idx


def update_row(ws, column_header, column_values, reason, date):
    row_idx = find_row(ws, column_header, column_values["SOURCE"], column_values["GEO"])
    if row_idx is None:
        row_idx = append_row(ws, column_header)
    for column_name, column_value in column_values.items():
        if column_name not in column_header:
            column_header.append(column_name)
            ws.cell(row=1, column=len(column_header)).value = column_name
        ws.cell(row=row_idx, column=column_header.index(column_name) + 1).value = column_value
    for column_name in column_header:  # zero the features no longer present
        if not column_name or column_name in column_values or column_name in MANUAL_COLUMNS:
            continue
        cell = ws.cell(row=row_idx, column=column_header.index(column_name) + 1)
        if cell.value:
            cell.value = 0
    ws.cell(row=row_idx, column=column_header.index("LAST_UPDATED") + 1).value = date
    ws.cell(row=row_idx, column=column_header.index("UPDATE_REASON") + 1).value = reason


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--stats", required=True, help="the workbook to update")
    parser.add_argument("--reason", required=True, help="UPDATE_REASON for every file given")
    parser.add_argument("--date", help="LAST_UPDATED as YYYY-MM-DD (default: today)")
    parser.add_argument("files", nargs="+", metavar="FILE")
    args = parser.parse_args(argv)

    date = datetime.datetime.strptime(args.date, "%Y-%m-%d") if args.date else (
        datetime.datetime.combine(datetime.date.today(), datetime.time()))
    try:
        for path in args.files:
            source_and_geo(path)
    except ValueError as error:
        print(f"stopped: {error}", file=sys.stderr)
        return 2
    if not os.path.exists(args.stats):
        print(f"stopped: {args.stats} not found", file=sys.stderr)
        return 2

    json_parser = json2attribute(CONFIG)
    wb = load_workbook(args.stats)
    ws = wb.worksheets[0]
    column_header = list(next(ws.values))
    for path in args.files:
        update_row(ws, column_header, file_stats(json_parser, path), args.reason, date)
        print(f"updated: {os.path.basename(path)}")
    wb.save(args.stats)
    return 0


if __name__ == "__main__":
    sys.exit(main())
