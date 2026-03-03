"""
Pydantic schemas for API
"""
from pydantic import BaseModel, Field
from typing import Optional, List, Any, Dict
from datetime import datetime, date
import uuid



# Customer Schemas
class CustomerOut(BaseModel):
    id: uuid.UUID
    name: str
    email: str
    external_id: Optional[str]
    created_at: datetime

    model_config = {"from_attributes": True}



# Pricing Schemas
class PricingRuleOut(BaseModel):
    id: uuid.UUID
    metric_name: str
    display_name: str
    unit_label: str
    pricing_model: str
    base_price: float
    free_tier_limit: int
    tiers: Optional[Any]

    model_config = {"from_attributes": True}


class PricingPlanOut(BaseModel):
    id: uuid.UUID
    name: str
    description: Optional[str]
    rules: List[PricingRuleOut] = []

    model_config = {"from_attributes": True}



# Ingestion Schemas
class IngestionJobOut(BaseModel):
    id: uuid.UUID
    customer_id: Optional[uuid.UUID]
    source_type: str
    original_filename: Optional[str]
    status: str
    row_count: int
    error_message: Optional[str]
    created_at: datetime

    model_config = {"from_attributes": True}


class FieldMappingOut(BaseModel):
    id: uuid.UUID
    source_field: str
    target_field: str
    confidence: Optional[float]
    mapping_method: Optional[str]
    is_confirmed: bool

    model_config = {"from_attributes": True}


class FieldMappingUpdate(BaseModel):
    mappings: List[Dict[str, Any]]  # [{source_field, target_field, is_confirmed}]



# Usage Record Schemas

class UsageRecordOut(BaseModel):
    id: uuid.UUID
    metric_name: str
    quantity: float
    unit: Optional[str]
    recorded_at: datetime
    is_anomaly: bool
    anomaly_reason: Optional[str]
    metadata: Dict

    model_config = {"from_attributes": True}



# Billing Preview Schemas

class BillingLineItem(BaseModel):
    metric_name: str
    display_name: str
    unit_label: str
    total_quantity: float
    billable_quantity: float
    free_tier_used: float
    pricing_model: str
    unit_price: Optional[float]
    amount: float
    tiers_breakdown: Optional[List[Dict]] = None


class BillingWarning(BaseModel):
    severity: str
    warning_type: str
    message: str
    metric_name: Optional[str]


class BillingPreviewOut(BaseModel):
    id: uuid.UUID
    job_id: uuid.UUID
    customer_id: Optional[uuid.UUID]
    period_start: date
    period_end: date
    subtotal: float
    total: float
    line_items: List[Dict]
    warnings: List[Dict]
    status: str
    created_at: datetime

    model_config = {"from_attributes": True}



# Validation Warning Schemas

class ValidationWarningOut(BaseModel):
    id: uuid.UUID
    severity: str
    warning_type: str
    message: str
    metric_name: Optional[str]
    affected_value: Optional[float]
    expected_range_low: Optional[float]
    expected_range_high: Optional[float]

    model_config = {"from_attributes": True}



# Ingestion Response

class IngestResponse(BaseModel):
    job_id: uuid.UUID
    status: str
    row_count: int
    columns_detected: List[str]
    suggested_mappings: List[FieldMappingOut]
    sample_rows: List[Dict]
    message: str


class PreviewResponse(BaseModel):
    preview: BillingPreviewOut
    warnings: List[ValidationWarningOut]
    usage_summary: List[Dict]


class ExportResponse(BaseModel):
    job_id: uuid.UUID
    format: str
    data: Any
    filename: str



# Mock Data Request
class MockDataRequest(BaseModel):
    customer_id: uuid.UUID
    scenario: str = "normal"  # normal | spike | messy | enterprise
    num_days: int = Field(default=30, ge=1, le=90)