"""
Usage Data Ingestion & Billing Preview Tool
FastAPI Backend
"""

from fastapi import FastAPI, UploadFile, File, HTTPException, Depends, Query, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from typing import Optional, List
import uuid
import json
import io
import csv
from datetime import datetime, timezone, date, timedelta

from config import settings
from database import get_db, Customer, PricingPlan, PricingRule, CustomerContract
from database import IngestionJob, FieldMapping, UsageRecord, BillingPreview, ValidationWarning
from schemas import (
    CustomerOut, PricingPlanOut, IngestionJobOut, FieldMappingOut, FieldMappingUpdate,
    UsageRecordOut, BillingPreviewOut, ValidationWarningOut,
    IngestResponse, PreviewResponse, ExportResponse, MockDataRequest
)
from field_mapper import ai_map_fields
from ingestion_service import parse_csv, parse_json, normalize_records, generate_mock_csv
from billing_engine import generate_billing_preview

app = FastAPI(
    title="Usage Data Ingestion & Billing Preview API",
    description="Ingest raw usage data, normalize it and generate billing previews",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)



# Health Check
@app.get("/health")
async def health():
    return {"status": "ok", "version": "1.0.0", "ai_enabled": bool(settings.OPENAI_API_KEY)}



# Customers
@app.get("/customers", response_model=List[CustomerOut])
async def list_customers(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Customer).order_by(Customer.name))
    return result.scalars().all()


@app.get("/customers/{customer_id}", response_model=CustomerOut)
async def get_customer(customer_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    customer = await db.get(Customer, customer_id)
    if not customer:
        raise HTTPException(404, "Customer not found")
    return customer


# Pricing Plans
@app.get("/pricing-plans", response_model=List[PricingPlanOut])
async def list_pricing_plans(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(PricingPlan).order_by(PricingPlan.name)
    )
    plans = result.scalars().all()

    # Load rules in a single query instead of lazy loading per plan
    all_rules_result = await db.execute(select(PricingRule))
    all_rules = all_rules_result.scalars().all()

    # Group rules by plan_id manually
    rules_by_plan = {}
    for rule in all_rules:
        pid = str(rule.plan_id)
        if pid not in rules_by_plan:
            rules_by_plan[pid] = []
        rules_by_plan[pid].append(rule)

    # Attach rules to plans
    for plan in plans:
        plan.rules = rules_by_plan.get(str(plan.id), [])

    return plans



# Mock Data Generation

# Generate mock CSV data for demo purposes
@app.post("/mock-data/generate")
async def generate_mock_data(request: MockDataRequest, db: AsyncSession = Depends(get_db)):
    csv_bytes = generate_mock_csv(scenario=request.scenario, num_days=request.num_days)

    filename = f"mock_{request.scenario}_{request.num_days}days.csv"

    return StreamingResponse(
        io.BytesIO(csv_bytes),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@app.get("/mock-data/scenarios")
async def list_scenarios():
    return {
        "scenarios": [
            {"id": "normal", "name": "Normal Usage", "description": "Clean, predictable daily usage"},
            {"id": "spike", "name": "Usage Spike", "description": "Normal data with one extreme outlier day"},
            {"id": "messy", "name": "Messy Data", "description": "Real-world messy CSV from homegrown system"},
            {"id": "enterprise", "name": "Enterprise Scale", "description": "High-volume enterprise usage"},
        ]
    }



# Data Ingestion

#Upload and analyze a CSV file
@app.post("/ingest/csv", response_model=IngestResponse)
async def ingest_csv(
    file: UploadFile = File(...),
    customer_id: Optional[uuid.UUID] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    is_csv = (
        file.filename.lower().endswith(".csv")
        or file.content_type in ["text/csv", "application/csv", "text/plain"]
    )
    if not is_csv:
        raise HTTPException(400, "Only CSV files are accepted")

    content = await file.read()
    if len(content) > 50 * 1024 * 1024:
        raise HTTPException(400, "File too large (max 50MB)")

    print(f"File: {file.filename} | Size: {len(content)} bytes | Type: {file.content_type}")

    try:
        columns, sample_rows, total_rows, all_rows = await parse_csv(content, file.filename)
    except ValueError as e:
        print(f"Parse error: {e}")
        raise HTTPException(400, str(e))

    return await _process_ingestion(
        db=db,
        raw_data={"columns": columns, "rows": [dict(r) for r in sample_rows[:100]]},
        source_type="csv",
        original_filename=file.filename,
        columns=columns,
        sample_rows=[dict(r) for r in sample_rows],
        total_rows=total_rows,
        customer_id=customer_id,
        all_rows=all_rows,
    )


# Upload and analyze a JSON file
@app.post("/ingest/json", response_model=IngestResponse)
async def ingest_json( file: UploadFile = File(...), customer_id: Optional[uuid.UUID] = Query(None), db: AsyncSession = Depends(get_db),):

    content = await file.read()

    try:
        columns, sample_rows, total_rows = await parse_json(content)
    except (ValueError, json.JSONDecodeError) as e:
        raise HTTPException(400, f"Invalid JSON: {str(e)}")

    return await _process_ingestion(
        db=db,
        raw_data={"columns": columns, "rows": sample_rows[:100]},
        source_type="json",
        original_filename=file.filename,
        columns=columns,
        sample_rows=sample_rows,
        total_rows=total_rows,
        customer_id=customer_id,
        all_rows=json.loads(content) if isinstance(json.loads(content), list) else sample_rows,
    )


# Accept a webhook payload
@app.post("/ingest/webhook", response_model=IngestResponse)
async def ingest_webhook( payload: dict, customer_id: Optional[uuid.UUID] = Query(None), db: AsyncSession = Depends(get_db), ):
    
    records = payload.get("records", payload.get("data", [payload]))
    if not isinstance(records, list):
        records = [records]

    columns = list(records[0].keys()) if records else []
    sample_rows = records[:5]

    return await _process_ingestion(
        db=db,
        raw_data=payload,
        source_type="webhook",
        original_filename="webhook",
        columns=columns,
        sample_rows=sample_rows,
        total_rows=len(records),
        customer_id=customer_id,
        all_rows=records,
    )


# Shared ingestion logi: create job, run AI field mapping
async def _process_ingestion( db: AsyncSession, raw_data: dict, source_type: str, original_filename: str, columns: List[str], sample_rows: List[dict], total_rows: int, customer_id: Optional[uuid.UUID], all_rows: List[dict], ) -> IngestResponse:

    # Create ingestion job
    job = IngestionJob(
        customer_id=customer_id,
        source_type=source_type,
        original_filename=original_filename,
        raw_data={
            **raw_data,
            "all_rows": all_rows[:10000],  # Store up to 10k rows in JSON
        },
        status="processing",
        row_count=total_rows,
    )
    db.add(job)
    await db.flush()

    # Build sample values for AI context
    sample_values = {}
    for col in columns:
        vals = []
        for row in sample_rows[:10]:
            v = row.get(col)
            if v is not None and str(v).strip():
                vals.append(str(v))
        sample_values[col] = vals[:5]

    # Run AI field mapping
    try:
        mapping_results = await ai_map_fields(columns, sample_values)
    except Exception as e:
        mapping_results = [{
            "source_field": col,
            "target_field": col.lower().replace(" ", "_"),
            "confidence": 0.3,
            "mapping_method": "fallback",
            "reasoning": str(e),
        } for col in columns]

    # Persist field mappings
    field_mappings = []
    for m in mapping_results:
        fm = FieldMapping(
            job_id=job.id,
            source_field=m["source_field"],
            target_field=m["target_field"],
            confidence=m.get("confidence", 0.5),
            mapping_method=m.get("mapping_method", "rule"),
            is_confirmed=m.get("confidence", 0) >= 0.85,  # Auto confirm high confidence
        )
        db.add(fm)
        field_mappings.append(fm)

    job.status = "mapped"
    await db.commit()

    return IngestResponse(
        job_id=job.id,
        status="mapped",
        row_count=total_rows,
        columns_detected=columns,
        suggested_mappings=[FieldMappingOut.model_validate(fm) for fm in field_mappings],
        sample_rows=sample_rows[:5],
        message=f"Detected {len(columns)} columns, {total_rows} rows. Review field mappings below.",
    )



# Field Mapping Management

@app.get("/jobs/{job_id}/mappings", response_model=List[FieldMappingOut])
async def get_mappings(job_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(FieldMapping).where(FieldMapping.job_id == job_id)
    )
    return result.scalars().all()

# Update/confirm field mappings
@app.put("/jobs/{job_id}/mappings")
async def update_mappings( job_id: uuid.UUID, body: FieldMappingUpdate, db: AsyncSession = Depends(get_db), ):
    for update in body.mappings:
        result = await db.execute(
            select(FieldMapping).where(
                FieldMapping.job_id == job_id,
                FieldMapping.source_field == update["source_field"]
            )
        )
        fm = result.scalar_one_or_none()
        if fm:
            fm.target_field = update.get("target_field", fm.target_field)
            fm.is_confirmed = update.get("is_confirmed", fm.is_confirmed)

    await db.commit()
    return {"status": "updated"}



# Normalize & Preview
# Normalize raw data using confirmed field mappings
@app.post("/jobs/{job_id}/normalize")
async def normalize_job(job_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    job = await db.get(IngestionJob, job_id)
    if not job:
        raise HTTPException(404, "Job not found")

    # Get confirmed mappings
    result = await db.execute(
        select(FieldMapping).where(FieldMapping.job_id == job_id)
    )
    mappings = result.scalars().all()
    mapping_dicts = [{"source_field": m.source_field, "target_field": m.target_field, "is_confirmed": m.is_confirmed} for m in mappings]

    # Get raw rows
    raw_rows = job.raw_data.get("all_rows", job.raw_data.get("rows", []))

    # Normalize
    normalized = await normalize_records(
        raw_rows=raw_rows,
        field_mappings=mapping_dicts,
        customer_id=str(job.customer_id) if job.customer_id else None,
    )

    # Delete old usage records for this job
    old_records = await db.execute(
        select(UsageRecord).where(UsageRecord.job_id == job_id)
    )
    for r in old_records.scalars():
        await db.delete(r)

    # Insert new usage records
    for rec in normalized:
        usage_record = UsageRecord(
            job_id=job_id,
            customer_id=job.customer_id,
            metric_name=rec["metric_name"],
            quantity=rec["quantity"],
            unit=rec.get("unit"),
            recorded_at=datetime.fromisoformat(rec["recorded_at"]) if isinstance(rec["recorded_at"], str) else rec["recorded_at"],
           extra_metadata=rec.get("extra_metadata", {}),

        )
        db.add(usage_record)

    job.status = "normalized"
    await db.commit()

    return {
        "status": "normalized",
        "records_created": len(normalized),
        "metrics_found": list(set(r["metric_name"] for r in normalized)),
    }

# Generate billing preview for a job
@app.post("/jobs/{job_id}/preview", response_model=PreviewResponse)
async def generate_preview( job_id: uuid.UUID, plan_id: Optional[uuid.UUID] = Query(None), period_start: Optional[date] = Query(None), period_end: Optional[date] = Query(None), db: AsyncSession = Depends(get_db), ):

    job = await db.get(IngestionJob, job_id)
    if not job:
        raise HTTPException(404, "Job not found")

    # Normalize if not done
    if job.status not in ("normalized", "preview", "committed"):
        await normalize_job(job_id, db)
        await db.refresh(job)

    # Get usage records
    records_result = await db.execute(
        select(UsageRecord).where(UsageRecord.job_id == job_id)
    )
    usage_records = records_result.scalars().all()

    if not usage_records:
        raise HTTPException(400, "No usage records found. Run normalize first.")

    # Get pricing plan
    if not plan_id:
        # Try to get from customer's contract
        if job.customer_id:
            contract_result = await db.execute(
                select(CustomerContract).where(
                    CustomerContract.customer_id == job.customer_id,
                    CustomerContract.is_active == True
                ).order_by(CustomerContract.created_at.desc()).limit(1)
            )
            contract = contract_result.scalar_one_or_none()
            if contract:
                plan_id = contract.plan_id

    if not plan_id:
        # Default to first available plan
        plan_result = await db.execute(select(PricingPlan).limit(1))
        plan = plan_result.scalar_one_or_none()
        if plan:
            plan_id = plan.id

    # Get pricing rules
    rules_result = await db.execute( select(PricingRule).where(PricingRule.plan_id == plan_id) )
    pricing_rules = rules_result.scalars().all()
    rules_dicts = [{
        "metric_name": r.metric_name,
        "display_name": r.display_name,
        "unit_label": r.unit_label,
        "pricing_model": r.pricing_model,
        "base_price": float(r.base_price),
        "free_tier_limit": r.free_tier_limit,
        "tiers": r.tiers,
    } for r in pricing_rules]

    # Determine billing period
    if not period_start:
        timestamps = [r.recorded_at for r in usage_records]
        period_start = min(timestamps).date()
    if not period_end:
        timestamps = [r.recorded_at for r in usage_records]
        period_end = max(timestamps).date()

    # Run billing engine
    records_dicts = [{
        "metric_name": r.metric_name,
        "quantity": float(r.quantity),
        "recorded_at": r.recorded_at.isoformat(),
    } for r in usage_records]

    billing_result = generate_billing_preview(
        usage_records=records_dicts,
        pricing_rules=rules_dicts,
        period_start=period_start,
        period_end=period_end,
    )

    # Get or create billing preview
    preview_result = await db.execute(
        select(BillingPreview).where(
            BillingPreview.job_id == job_id,
            BillingPreview.status == "preview"
        )
    )
    preview = preview_result.scalar_one_or_none()

    if preview:
        preview.subtotal = billing_result["subtotal"]
        preview.total = billing_result["total"]
        preview.line_items = billing_result["line_items"]
        preview.warnings = billing_result["warnings"]
        preview.period_start = period_start
        preview.period_end = period_end
    else:
        preview = BillingPreview(
            job_id=job_id,
            customer_id=job.customer_id,
            period_start=period_start,
            period_end=period_end,
            subtotal=billing_result["subtotal"],
            total=billing_result["total"],
            line_items=billing_result["line_items"],
            warnings=billing_result["warnings"],
            status="preview",
        )
        db.add(preview)

    # Save validation warnings
    existing_warnings = await db.execute(
        select(ValidationWarning).where(ValidationWarning.job_id == job_id)
    )
    for w in existing_warnings.scalars():
        await db.delete(w)

    for w in billing_result["warnings"]:
        vw = ValidationWarning(
            job_id=job_id,
            severity=w["severity"],
            warning_type=w["warning_type"],
            message=w["message"],
            metric_name=w.get("metric_name"),
            affected_value=w.get("affected_value"),
            expected_range_low=w.get("expected_range_low"),
            expected_range_high=w.get("expected_range_high"),
        )
        db.add(vw)

    job.status = "preview"
    await db.commit()

    # Usage summary
    usage_summary = []
    for metric_name, total in billing_result["metric_totals"].items():
        usage_summary.append({"metric_name": metric_name, "total": total})

    # Get warnings
    warnings_result = await db.execute(
        select(ValidationWarning).where(ValidationWarning.job_id == job_id)
    )
    warnings = warnings_result.scalars().all()

    return PreviewResponse(
        preview=BillingPreviewOut.model_validate(preview),
        warnings=[ValidationWarningOut.model_validate(w) for w in warnings],
        usage_summary=usage_summary,
    )



# Export

#Export normalized data in billing systems' compatible formats
@app.post("/jobs/{job_id}/export")
async def export_job(job_id: uuid.UUID, format: str = Query("standard", regex="^(standard|csv|json)$"), db: AsyncSession = Depends(get_db), ):
    
    job = await db.get(IngestionJob, job_id)
    if not job:
        raise HTTPException(404, "Job not found")

    records_result = await db.execute(
        select(UsageRecord).where(UsageRecord.job_id == job_id).order_by(UsageRecord.recorded_at)
    )
    records = records_result.scalars().all()

    preview_result = await db.execute(
        select(BillingPreview).where(BillingPreview.job_id == job_id).order_by(BillingPreview.created_at.desc()).limit(1)
    )
    preview = preview_result.scalar_one_or_none()

    if format == "standard":
        data = {
            "schema_version": "1.0",
            "exported_at": datetime.now(tz=timezone.utc).isoformat(),
            "source": {
                "job_id": str(job_id),
                "original_file": job.original_filename,
                "source_type": job.source_type,
            },
            "billing_period": {
                "start": str(preview.period_start) if preview else None,
                "end": str(preview.period_end) if preview else None,
            },
            "usage_records": [
                {
                    "id": str(r.id),
                    "metric": r.metric_name,
                    "quantity": float(r.quantity),
                    "unit": r.unit,
                    "timestamp": r.recorded_at.isoformat(),
                    "customer_id": str(r.customer_id) if r.customer_id else None,
                }
                for r in records
            ],
            "billing_preview": {
                "total": float(preview.total) if preview else 0,
                "line_items": preview.line_items if preview else [],
            } if preview else None,
        }
        filename = f"billing_export_{job_id}.json"
        return StreamingResponse(
            io.BytesIO(json.dumps(data, indent=2).encode()),
            media_type="application/json",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )

    elif format == "csv":
        output = io.StringIO()
        fieldnames = ["id", "metric_name", "quantity", "unit", "recorded_at", "customer_id"]
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        for r in records:
            writer.writerow({
                "id": str(r.id),
                "metric_name": r.metric_name,
                "quantity": float(r.quantity),
                "unit": r.unit or "",
                "recorded_at": r.recorded_at.isoformat(),
                "customer_id": str(r.customer_id) if r.customer_id else "",
            })
        filename = f"normalized_usage_{job_id}.csv"
        return StreamingResponse(
            io.BytesIO(output.getvalue().encode()),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )

    elif format == "json":
        data = [
            {
                "metric": r.metric_name,
                "quantity": float(r.quantity),
                "timestamp": r.recorded_at.isoformat(),
            }
            for r in records
        ]
        filename = f"usage_records_{job_id}.json"
        return StreamingResponse(
            io.BytesIO(json.dumps(data, indent=2).encode()),
            media_type="application/json",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )



# Jobs List

@app.get("/jobs", response_model=List[IngestionJobOut])
async def list_jobs( customer_id: Optional[uuid.UUID] = Query(None), limit: int = Query(20, ge=1, le=100), db: AsyncSession = Depends(get_db),):
    
    query = select(IngestionJob).order_by(IngestionJob.created_at.desc()).limit(limit)
    if customer_id:
        query = query.where(IngestionJob.customer_id == customer_id)
    result = await db.execute(query)
    return result.scalars().all()


@app.get("/jobs/{job_id}", response_model=IngestionJobOut)
async def get_job(job_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    job = await db.get(IngestionJob, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return job


@app.get("/jobs/{job_id}/usage-records", response_model=List[UsageRecordOut])
async def get_usage_records(
    job_id: uuid.UUID,
    metric: Optional[str] = Query(None),
    limit: int = Query(100),
    db: AsyncSession = Depends(get_db),
):
    query = select(UsageRecord).where(UsageRecord.job_id == job_id).limit(limit)
    if metric:
        query = query.where(UsageRecord.metric_name == metric)
    result = await db.execute(query)
    return result.scalars().all()