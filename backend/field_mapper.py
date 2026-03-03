"""
AI field mapping service using OpenAI
Maps arbitrary source column names to normalized billing metrics
"""

import json
import re
from typing import List, Dict, Tuple
from openai import AsyncOpenAI
from config import settings


# Known canonical metrics and their common aliases
CANONICAL_METRICS = {
    "api_calls": ["api_requests", "api_hits", "request_count", "requests", "api_count",
                   "num_requests", "total_requests", "api_usage", "calls", "http_requests",
                   "endpoint_calls", "api_calls_made"],
    "compute_hours": ["cpu_hours", "compute_time", "runtime_hours", "execution_hours",
                       "processing_hours", "instance_hours", "vm_hours", "worker_hours",
                       "job_duration", "compute_duration"],
    "storage_gb": ["storage_used", "disk_usage", "data_stored", "storage_usage",
                    "bytes_stored", "storage_bytes", "disk_gb", "storage_size",
                    "data_size", "stored_gb"],
    "active_seats": ["users", "active_users", "user_count", "seat_count", "licenses",
                      "num_users", "total_users", "user_seats", "active_licenses",
                      "monthly_active_users", "mau"],
    "data_transfer_gb": ["bandwidth", "data_transferred", "network_egress", "egress_gb",
                          "data_out", "transfer_gb", "outbound_data", "network_gb",
                          "bytes_transferred", "data_egress"],
    "timestamp": ["date", "time", "datetime", "recorded_at", "event_time", "ts",
                   "created_at", "log_time", "period", "billing_date", "usage_date"],
    "customer_id": ["customer", "account_id", "client_id", "org_id", "organization_id",
                     "tenant_id", "user_id", "account", "client"],
    "quantity": ["count", "value", "amount", "total", "usage", "volume", "units"],
}

# Fast rule-based mapping before calling OpenAI
# Returns (target_field, confidence)
def rule_based_mapping(source_field: str) -> Tuple[str, float]:
    normalized = source_field.lower().strip().replace(" ", "_").replace("-", "_")

    # Exact match
    for canonical, aliases in CANONICAL_METRICS.items():
        if normalized == canonical:
            return canonical, 1.0
        if normalized in aliases:
            return canonical, 0.95

    # Partial match
    for canonical, aliases in CANONICAL_METRICS.items():
        all_terms = [canonical] + aliases
        for term in all_terms:
            if term in normalized or normalized in term:
                return canonical, 0.75

    return "unknown", 0.0

# Use OpenAI to intelligently map soure fields to canonical billing metrics
#  Returns list of {source_field, target_field, confidence, reasoning}
async def ai_map_fields( source_fields: List[str], sample_values: Dict[str, List],) -> List[Dict]:
    # First try rule-based for each field
    results = []
    fields_needing_ai = []

    for field in source_fields:
        target, confidence = rule_based_mapping(field)
        if confidence >= 0.75:
            results.append({
                "source_field": field,
                "target_field": target,
                "confidence": confidence,
                "mapping_method": "rule",
                "reasoning": f"Pattern match: '{field}' → '{target}'",
            })
        else:
            fields_needing_ai.append(field)

    # For remaining fields, use OpenAI if available
    if fields_needing_ai and settings.OPENAI_API_KEY:
        try:
            ai_results = await _openai_map_fields(fields_needing_ai, sample_values)
            results.extend(ai_results)
        except Exception as e:
            # Fallback to best-guess rule-based
            for field in fields_needing_ai:
                results.append({
                    "source_field": field,
                    "target_field": "unknown",
                    "confidence": 0.1,
                    "mapping_method": "fallback",
                    "reasoning": f"AI mapping failed: {str(e)}",
                })
    else:
        # No AI, use low-confidence rule results
        for field in fields_needing_ai:
            target, confidence = rule_based_mapping(field)
            results.append({
                "source_field": field,
                "target_field": target if target != "unknown" else field.lower().replace(" ", "_"),
                "confidence": max(confidence, 0.2),
                "mapping_method": "rule",
                "reasoning": "Rule-based mapping (AI not configured)",
            })

    return results

# Call OpenAI for field mapping
async def _openai_map_fields( fields: List[str], sample_values: Dict[str, List], ) -> List[Dict]:
    client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)

    canonical_list = "\n".join(
        f"- {name}: {', '.join(aliases[:4])}" for name, aliases in CANONICAL_METRICS.items()
    )

    # Build sample context
    samples_context = ""
    for field in fields:
        if field in sample_values and sample_values[field]:
            samples_context += f"  '{field}': {sample_values[field][:3]}\n"

    prompt = f"""You are a data normalization expert for a usage-based billing system.

Map the following source column names to our canonical billing metrics.

CANONICAL METRICS (name: example aliases):
{canonical_list}

SOURCE FIELDS TO MAP:
{json.dumps(fields)}

SAMPLE VALUES FROM THE DATA:
{samples_context}

For each source field, provide the best canonical metric match with a confidence score (0.0-1.0).
If no good match exists, use the original field name normalized to snake_case.

Respond with ONLY valid JSON, no markdown, no explanation:
{{
  "mappings": [
    {{
      "source_field": "original name",
      "target_field": "canonical_metric_name",
      "confidence": 0.95,
      "reasoning": "brief explanation"
    }}
  ]
}}"""

    response = await client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.1,
        response_format={"type": "json_object"},
    )

    content = response.choices[0].message.content
    data = json.loads(content)

    return [
        {
            **m,
            "mapping_method": "ai",
        }
        for m in data.get("mappings", [])
    ]

#  Normalize a raw value based on its target field type
def normalize_value(value: str, field_type: str) -> any:
    if value is None or str(value).strip() == "":
        return None

    str_val = str(value).strip()

    if field_type in ["api_calls", "active_seats"]:
        # Integer metrics
        cleaned = re.sub(r"[,\s]", "", str_val)
        cleaned = re.sub(r"[^\d.]", "", cleaned)
        try:
            return int(float(cleaned))
        except (ValueError, TypeError):
            return 0

    elif field_type in ["compute_hours", "storage_gb", "data_transfer_gb", "quantity"]:
        # Float metrics
        cleaned = re.sub(r"[,\s]", "", str_val)
        # Handle units like "10GB", "5.2 hours"
        cleaned = re.sub(r"[a-zA-Z]+$", "", cleaned).strip()
        try:
            return float(cleaned)
        except (ValueError, TypeError):
            return 0.0

    elif field_type == "timestamp":
        # Date/time parsing handled in ingestion service
        return str_val

    else:
        return str_val