"""
Billing engine calculates preview invoices
"""

from typing import List, Dict, Optional, Tuple
from decimal import Decimal, ROUND_HALF_UP
import statistics

# Calculate cost using tiered pricing 
# Each tier: {"up_to": int|null, "price": float}
def calculate_tiered_price(quantity: float, tiers: List[Dict]) -> Tuple[float, List[Dict]]:
    total_cost = 0.0
    remaining = quantity
    breakdown = []
    prev_up_to = 0

    for tier in tiers:
        if remaining <= 0:
            break

        up_to = tier.get("up_to")
        price = float(tier["price"])

        if up_to is None:
            # Last tier is unlimited
            tier_units = remaining
        else:
            tier_capacity = up_to - prev_up_to
            tier_units = min(remaining, tier_capacity)

        tier_cost = tier_units * price
        total_cost += tier_cost

        breakdown.append({
            "tier_start": prev_up_to,
            "tier_end": up_to,
            "units": round(tier_units, 4),
            "unit_price": price,
            "tier_cost": round(tier_cost, 6),
        })

        remaining -= tier_units
        if up_to is not None:
            prev_up_to = up_to

    return round(total_cost, 6), breakdown


# Volume pricing: the entire quantity gets the price of the tier it falls into
def calculate_volume_price(quantity: float, tiers: List[Dict]) -> Tuple[float, List[Dict]]:
    for tier in tiers:
        up_to = tier.get("up_to")
        price = float(tier["price"])
        if up_to is None or quantity <= up_to:
            return round(quantity * price, 6), [{"unit_price": price, "units": quantity}]

    # Shouldn't reach here but fallback
    last_price = float(tiers[-1]["price"])
    return round(quantity * last_price, 6), [{"unit_price": last_price, "units": quantity}]


#  Compute a single billing line item for a metric

def compute_line_item( metric_name: str, display_name: str, unit_label: str, total_quantity: float, pricing_rule: Dict, ) -> Dict:
    
    free_tier = float(pricing_rule.get("free_tier_limit", 0))
    billable_quantity = max(0, total_quantity - free_tier)
    free_tier_used = min(total_quantity, free_tier)

    pricing_model = pricing_rule["pricing_model"]
    base_price = float(pricing_rule.get("base_price", 0))
    tiers = pricing_rule.get("tiers")

    amount = 0.0
    unit_price = None
    tiers_breakdown = None

    if billable_quantity <= 0:
        # All covered by free tier
        amount = 0.0
        unit_price = base_price

    elif pricing_model == "flat_rate":
        unit_price = base_price
        amount = billable_quantity * base_price

    elif pricing_model == "tiered" and tiers:
        amount, tiers_breakdown = calculate_tiered_price(billable_quantity, tiers)

    elif pricing_model == "volume" and tiers:
        amount, tiers_breakdown = calculate_volume_price(billable_quantity, tiers)
        if tiers_breakdown:
            unit_price = tiers_breakdown[0].get("unit_price")

    elif pricing_model == "package":
        # Package pricing: round up to nearest package
        package_size = pricing_rule.get("package_size", 1)
        package_price = base_price
        packages_needed = -(-billable_quantity // package_size)  # Ceiling division
        amount = packages_needed * package_price
        unit_price = package_price

    # Round to 2 decimal places for display
    amount = float(Decimal(str(amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

    return {
        "metric_name": metric_name,
        "display_name": display_name,
        "unit_label": unit_label,
        "total_quantity": round(total_quantity, 4),
        "billable_quantity": round(billable_quantity, 4),
        "free_tier_used": round(free_tier_used, 4),
        "pricing_model": pricing_model,
        "unit_price": unit_price,
        "amount": amount,
        "tiers_breakdown": tiers_breakdown,
    }


# Detect usage anomalies: spikes, zeros, negative values.
# Returns list of warning dicts.
def detect_anomalies( metric_records: List[Dict], historical_avg: Optional[float] = None, ) -> List[Dict]:
   
    warnings = []
    if not metric_records:
        return warnings

    quantities = [r["quantity"] for r in metric_records]
    metric_name = metric_records[0].get("metric_name", "unknown")

    # Check for negative values
    negatives = [q for q in quantities if q < 0]
    if negatives:
        warnings.append({
            "severity": "critical",
            "warning_type": "negative_value",
            "message": f"{metric_name}: {len(negatives)} record(s) have negative values",
            "metric_name": metric_name,
            "affected_value": min(negatives),
        })

    # Check for spikes using z-score or historical comparison
    if len(quantities) >= 3:
        avg = statistics.mean(quantities)
        try:
            stdev = statistics.stdev(quantities)
        except statistics.StatisticsError:
            stdev = 0

        if stdev > 0:
            for record in metric_records:
                z = (record["quantity"] - avg) / stdev
                if abs(z) > 3:
                    warnings.append({
                        "severity": "warning",
                        "warning_type": "spike",
                        "message": f"{metric_name}: Unusual spike detected — {record['quantity']:,.2f} (avg: {avg:,.2f}, {abs(z):.1f}x std dev)",
                        "metric_name": metric_name,
                        "affected_value": record["quantity"],
                        "expected_range_low": max(0, avg - 2 * stdev),
                        "expected_range_high": avg + 2 * stdev,
                    })

    # Historical comparison
    if historical_avg and historical_avg > 0:
        total = sum(quantities)
        ratio = total / historical_avg
        if ratio > 10:
            warnings.append({
                "severity": "critical",
                "warning_type": "spike",
                "message": f"{metric_name}: Total usage is {ratio:.1f}x historical average — verify data",
                "metric_name": metric_name,
                "affected_value": total,
                "expected_range_high": historical_avg * 3,
            })
        elif ratio > 3:
            warnings.append({
                "severity": "warning",
                "warning_type": "spike",
                "message": f"{metric_name}: Usage is {ratio:.1f}x higher than historical average",
                "metric_name": metric_name,
                "affected_value": total,
                "expected_range_high": historical_avg * 3,
            })

    return warnings


#  Main billing engine: aggregates usage and applies pricing rules
#  Returns a complete billing preview dict
def generate_billing_preview( usage_records: List[Dict], pricing_rules: List[Dict], period_start, period_end, ) -> Dict:
    # Aggregate usage by metric
    metric_totals: Dict[str, float] = {}
    metric_records: Dict[str, List] = {}

    for record in usage_records:
        metric = record["metric_name"]
        qty = float(record.get("quantity", 0))

        if metric not in metric_totals:
            metric_totals[metric] = 0.0
            metric_records[metric] = []

        metric_totals[metric] += qty
        metric_records[metric].append(record)

    # Compute line items
    line_items = []
    all_warnings = []

    # Track unknown metrics
    priced_metrics = {rule["metric_name"] for rule in pricing_rules}
    for metric in metric_totals:
        if metric not in priced_metrics and metric not in ["timestamp", "customer_id", "unknown"]:
            all_warnings.append({
                "severity": "info",
                "warning_type": "unknown_metric",
                "message": f"Metric '{metric}' has no pricing rule — excluded from invoice",
                "metric_name": metric,
            })

    for rule in pricing_rules:
        metric_name = rule["metric_name"]
        total_qty = metric_totals.get(metric_name, 0.0)

        # Detect anomalies for this metric
        if metric_name in metric_records:
            anomaly_warnings = detect_anomalies(metric_records[metric_name])
            all_warnings.extend(anomaly_warnings)

        # Always include metric in preview even if 0 usage
        line_item = compute_line_item(
            metric_name=metric_name,
            display_name=rule["display_name"],
            unit_label=rule["unit_label"],
            total_quantity=total_qty,
            pricing_rule=rule,
        )
        line_items.append(line_item)

    # Calculate totals
    subtotal = sum(item["amount"] for item in line_items)
    total = float(Decimal(str(subtotal)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

    # Add usage summary warnings
    if total == 0 and metric_totals:
        all_warnings.append({
            "severity": "info",
            "warning_type": "zero_invoice",
            "message": "All usage falls within free tiers — invoice total is $0.00",
        })

    return {
        "line_items": line_items,
        "warnings": all_warnings,
        "subtotal": subtotal,
        "total": total,
        "period_start": str(period_start),
        "period_end": str(period_end),
        "metric_totals": metric_totals,
    }