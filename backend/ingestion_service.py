"""
Data ingestion service handles CSV, JSON, webhook input formats and normalizes to usage records.
"""


import io
import csv
import json
import random
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Optional, Tuple, Any
from dateutil import parser as dateutil_parser
import pandas as pd

from field_mapper import ai_map_fields, normalize_value


MAX_ROWS = 100_000

# Parse CSV file content.
# Returns (columns, sample_rows, total_row_count, all_rows).
async def parse_csv(file_content: bytes, filename: str):
    content = None
    for encoding in ["utf-8-sig", "utf-8", "latin-1", "cp1252"]:
        try:
            content = file_content.decode(encoding)
            break
        except (UnicodeDecodeError, LookupError):
            continue

    if not content:
        raise ValueError("Could not decode file")

    content = content.replace("\r\n", "\n").replace("\r", "\n").strip()

    # Strip wrapping quotes from the entire content if present
    lines = content.split("\n")
    cleaned_lines = []
    for line in lines:
        line = line.strip()
        # If the whole line is wrapped in quotes, unwrap it
        if line.startswith('"') and line.endswith('"') and line.count('"') == 2:
            line = line[1:-1]
        cleaned_lines.append(line)
    content = "\n".join(cleaned_lines)

    first_line = content.split("\n")[0]
    delimiter_counts = {
        ",": first_line.count(","),
        ";": first_line.count(";"),
        "\t": first_line.count("\t"),
        "|": first_line.count("|"),
    }
    delimiter = max(delimiter_counts, key=delimiter_counts.get)

    reader = csv.DictReader(io.StringIO(content), delimiter=delimiter)
    all_rows = []
    for row in reader:
        cleaned = {k.strip().lstrip("\ufeff"): v for k, v in row.items() if k and k.strip()}
        if cleaned:
            all_rows.append(cleaned)

    if not all_rows:
        raise ValueError("No data rows found in CSV")

    columns = list(all_rows[0].keys())
    return columns, all_rows[:5], len(all_rows), all_rows


#Parse JSON file content.
# Handles: array of objects, {data: []}, {records: []}, {usage: []}
async def parse_json(file_content: bytes) -> Tuple[List[str], List[Dict], int]:
 
    data = json.loads(file_content.decode("utf-8"))

    # Extract records from common JSON structures
    if isinstance(data, list):
        records = data
    elif isinstance(data, dict):
        for key in ["data", "records", "usage", "events", "items", "results"]:
            if key in data and isinstance(data[key], list):
                records = data[key]
                break
        else:
            # Treat dict as single record
            records = [data]
    else:
        raise ValueError("JSON must be an array or object with a data array")

    if not records:
        raise ValueError("No records found in JSON")

    # Flatten nested objects one level
    flat_records = []
    for r in records:
        flat = {}
        for k, v in r.items():
            if isinstance(v, dict):
                for sub_k, sub_v in v.items():
                    flat[f"{k}_{sub_k}"] = sub_v
            else:
                flat[k] = v
        flat_records.append(flat)

    columns = list(flat_records[0].keys()) if flat_records else []
    return columns, flat_records[:5], len(flat_records)


# Parse various timestamp formats
def parse_timestamp(value: Any) -> Optional[datetime]:
    if value is None:
        return None

    str_val = str(value).strip()
    if not str_val:
        return None

    try:
        # Unix timestamp
        if str_val.replace(".", "").isdigit():
            ts = float(str_val)
            if ts > 1e12:  # milliseconds
                ts /= 1000
            return datetime.fromtimestamp(ts, tz=timezone.utc)

        return dateutil_parser.parse(str_val, fuzzy=True).replace(tzinfo=timezone.utc)
    except Exception:
        return datetime.now(tz=timezone.utc)


# Apply confirmed field mappings to normalize raw rows into usage records. 
# Returns list of normalized usage record dicts
async def normalize_records( raw_rows: List[Dict], field_mappings: List[Dict], customer_id: Optional[str] = None, ) -> List[Dict]:

    # Build mapping lookup
    mapping_lookup = {}
    for m in field_mappings:
        if m.get("is_confirmed", True) and m["target_field"] not in ("unknown", None, ""):
            mapping_lookup[m["source_field"]] = m["target_field"]

    normalized = []

    for row in raw_rows[:MAX_ROWS]:
        # Apply mappings
        mapped = {}
        extra = {}

        for source_field, value in row.items():
            target = mapping_lookup.get(source_field)
            if target:
                mapped[target] = value
            else:
                extra[source_field] = value

        # Determine timestamp
        timestamp_val = mapped.get("timestamp") or mapped.get("recorded_at")
        recorded_at = parse_timestamp(timestamp_val) or datetime.now(tz=timezone.utc)

        # Find the quantity/metric
        # Multiple metrics could be in one row (wide format) or one per row (long format)

        # Wide format: each metric column is separate
        metric_fields = [
            "api_calls", "compute_hours", "storage_gb", "active_seats",
            "data_transfer_gb", "quantity"
        ]

        metrics_found = []
        for field in metric_fields:
            if field in mapped and mapped[field] is not None:
                try:
                    qty = float(str(mapped[field]).replace(",", "").strip() or 0)
                except (ValueError, TypeError):
                    qty = 0

                if field == "quantity":
                    # Try to get metric name from a "metric_name" or similar field
                    metric_name = (
                        mapped.get("metric_name") or
                        mapped.get("metric") or
                        extra.get("type") or
                        "quantity"
                    )
                    metric_name = str(metric_name).lower().replace(" ", "_").replace("-", "_")
                else:
                    metric_name = field

                metrics_found.append({
                    "metric_name": metric_name,
                    "quantity": qty,
                    "unit": None,
                    "recorded_at": recorded_at.isoformat(),
                    "extra_metadata": {k: str(v) for k, v in extra.items()},
                    "customer_id": customer_id,
                })

        if metrics_found:
            normalized.extend(metrics_found)
        elif mapped:
            # Fallback: try to extract any numeric field
            for k, v in mapped.items():
                if k in ("timestamp", "customer_id"):
                    continue
                try:
                    qty = float(str(v).replace(",", ""))
                    normalized.append({
                        "metric_name": k,
                        "quantity": qty,
                        "unit": None,
                        "recorded_at": recorded_at.isoformat(),
                        "extra_metadata": {},
                        "customer_id": customer_id,
                    })
                except (ValueError, TypeError):
                    pass

    return normalized



# Mock Data Generator

#Generate realistic messy CSV data for demo purposes
def generate_mock_csv(scenario: str = "normal", num_days: int = 30) -> bytes:

    scenarios = {
        "normal": _generate_normal_data,
        "spike": _generate_spike_data,
        "messy": _generate_messy_data,
        "enterprise": _generate_enterprise_data,
    }

    generator = scenarios.get(scenario, _generate_normal_data)
    rows, headers = generator(num_days)

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=headers)
    writer.writeheader()
    writer.writerows(rows)

    return output.getvalue().encode("utf-8")

# Clean, normal usage data
def _generate_normal_data(num_days: int):
    headers = ["date", "api_requests", "compute_hours", "storage_used_gb",
               "active_users", "bandwidth_gb"]
    rows = []
    base_date = datetime.now() - timedelta(days=num_days)

    for i in range(num_days):
        day = base_date + timedelta(days=i)
        rows.append({
            "date": day.strftime("%Y-%m-%d"),
            "api_requests": random.randint(45000, 85000),
            "compute_hours": round(random.uniform(8, 24), 2),
            "storage_used_gb": round(random.uniform(45, 60), 2),
            "active_users": random.randint(12, 18),
            "bandwidth_gb": round(random.uniform(20, 50), 2),
        })

    return rows, headers

# Data with a noticeable usage spike.
def _generate_spike_data(num_days: int):

    headers = ["timestamp", "API_REQUESTS", "COMPUTE_HRS", "STORAGE_GB", "SEATS", "EGRESS_GB"]
    rows = []
    base_date = datetime.now() - timedelta(days=num_days)
    spike_day = random.randint(num_days // 2, num_days - 5)

    for i in range(num_days):
        day = base_date + timedelta(days=i)
        multiplier = 15.0 if i == spike_day else 1.0

        rows.append({
            "timestamp": day.strftime("%m/%d/%Y"),
            "API_REQUESTS": int(random.randint(50000, 80000) * multiplier),
            "COMPUTE_HRS": round(random.uniform(10, 20) * (multiplier if multiplier > 1 else 1), 2),
            "STORAGE_GB": round(random.uniform(40, 55), 2),
            "SEATS": random.randint(10, 15),
            "EGRESS_GB": round(random.uniform(25, 45) * (multiplier ** 0.5), 2),
        })

    return rows, headers

# Messy, inconsistent CSV like from a homegrown system.
def _generate_messy_data(num_days: int):
   
    headers = ["Date (UTC)", "# API Calls Made", "CPU Time (hours)",
               "Disk Usage (GB)", "# Licensed Users", "Net Transfer Out (GB)", "Notes"]
    rows = []
    base_date = datetime.now() - timedelta(days=num_days)

    for i in range(num_days):
        day = base_date + timedelta(days=i)

        # Add some noise: missing values, weird formatting
        api_calls = random.randint(40000, 90000)
        if random.random() < 0.05:
            api_calls = ""  # Missing value

        compute = round(random.uniform(8, 20), 2)
        if random.random() < 0.1:
            compute = f"{compute} hrs"  # Units in value

        storage = round(random.uniform(38, 62), 1)

        rows.append({
            "Date (UTC)": day.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "# API Calls Made": api_calls,
            "CPU Time (hours)": compute,
            "Disk Usage (GB)": storage,
            "# Licensed Users": random.randint(8, 20),
            "Net Transfer Out (GB)": round(random.uniform(15, 55), 2),
            "Notes": "auto-generated" if random.random() < 0.3 else "",
        })

    return rows, headers

# Enterprise-scale usage data
def _generate_enterprise_data(num_days: int):

    headers = ["event_time", "endpoint_calls", "vm_hours",
               "stored_gb", "mau", "data_out_gb"]
    rows = []
    base_date = datetime.now() - timedelta(days=num_days)

    for i in range(num_days):
        day = base_date + timedelta(days=i)
        rows.append({
            "event_time": day.isoformat() + "Z",
            "endpoint_calls": f"{random.randint(800000, 2500000):,}",  # With commas
            "vm_hours": round(random.uniform(200, 500), 1),
            "stored_gb": round(random.uniform(800, 1200), 2),
            "mau": random.randint(500, 800),
            "data_out_gb": round(random.uniform(300, 800), 2),
        })

    return rows, headers