from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_, func
from datetime import datetime, timezone, timedelta
import pytz
from typing import Optional, List
import traceback

from core.database import get_db
from core.auth import verify_firebase_token
from core.utils import register_action_log

# Import English models
from models import *

# Import updated schemas
from schemas.operations import (
    CustomerHistoryCreate, CustomerHistoryResponse,
    AppointmentCreate, 
    UsageAuditLogCreate
)
from schemas.users import TagResponse

router = APIRouter(dependencies=[Depends(verify_firebase_token)])

@router.get("/")
def get_operation_history(
    start_date: str = Query(..., description="YYYY-MM-DD"),
    end_date: str = Query(..., description="YYYY-MM-DD"),
    timezone_name: str = "America/Guayaquil",
    db: Session = Depends(get_db),
    token_data: dict = Depends(verify_firebase_token)
):
    # 1. Convert strings to datetime early for validation
    try:
        start_dt_naive = datetime.strptime(start_date[:10], "%Y-%m-%d")
        end_dt_naive = datetime.strptime(end_date[:10], "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid_date_format_use_YYYY_MM_DD")

    # 2. Validate the date range (max 45 days and logical order)
    delta_days = (end_dt_naive - start_dt_naive).days
    
    if delta_days < 0:
        raise HTTPException(status_code=400, detail="start_date_cannot_be_after_end_date")
        
    if delta_days > 45:
        # Aquí está el error exacto que solicitaste
        raise HTTPException(status_code=400, detail="range_too_long_max_45_days")

    try:
        establishment_id = token_data.get('uid')
        local_tz = pytz.timezone(timezone_name)

        # 3. Boundary adjustment
        def get_utc_boundary(dt, is_end=False):
            if is_end:
                # Go to the very last microsecond of the user's day
                dt = dt.replace(hour=23, minute=59, second=59, microsecond=999999)
            else:
                dt = dt.replace(hour=0, minute=0, second=0, microsecond=0)
            
            # Localize to the requested timezone and convert to UTC for querying
            return local_tz.localize(dt).astimezone(pytz.UTC)

        start_dt_utc = get_utc_boundary(start_dt_naive)
        end_dt_utc = get_utc_boundary(end_dt_naive, is_end=True)

        # 4. Database Query
        records = db.query(CustomerHistory).filter(
            and_(
                CustomerHistory.establishment_id == establishment_id,
                CustomerHistory.created_at >= start_dt_utc,
                CustomerHistory.created_at <= end_dt_utc
            )
        ).order_by(CustomerHistory.created_at.desc()).all()

        # 5. Flat response with original names and 'notes'
        return [
            {
                "created_at": r.created_at.astimezone(local_tz).isoformat(),
                "process_name": r.process_name,
                "income": float(r.income) if r.income else 0.0,
                "profile_id": r.profile_id,
                "customer_id": r.customer_id,
                "notes": r.notes
            }
            for r in records
        ]

    except HTTPException:
        # Re-raise the 400 errors triggered above
        raise
    except Exception as e:
        print(f"Error fetching history: {e}")
        raise HTTPException(status_code=500, detail="internal_server_error_fetching_history")

@router.post("/", status_code=status.HTTP_201_CREATED)
def add_service_record(
    data: CustomerHistoryCreate, 
    request: Request,
    db: Session = Depends(get_db), 
    token_data: dict = Depends(verify_firebase_token)
):
    """
    Registers a new service or sale linked to a customer and an establishment.
    """
    establishment_id = token_data.get('uid')

    try:
        new_record = CustomerHistory(
            **data.model_dump(), 
            establishment_id=establishment_id,
            created_at=datetime.now(timezone.utc)
        )
        
        db.add(new_record)
        db.commit()
        db.refresh(new_record)
        
        register_action_log(db, establishment_id, "CREATE_SERVICE_RECORD", "POST", "/operations/history", data.model_dump())
        
        return {"status": "success", "id": new_record.id}

    except Exception as e:
        db.rollback()
        print(f"Error detected: {e}") 
        raise HTTPException(status_code=500, detail=str(e))
    


@router.get("/{customer_id}", response_model=list[CustomerHistoryResponse])
def get_customer_operation_history(
    customer_id: int,
    timezone_name: str = Query("America/Guayaquil"),
    db: Session = Depends(get_db),
    token_data: dict = Depends(verify_firebase_token)
):
    try:
        establishment_id = token_data.get('uid')
        local_tz = pytz.timezone(timezone_name)

        records = db.query(CustomerHistory).filter(
            and_(
                CustomerHistory.customer_id == customer_id,
                CustomerHistory.establishment_id == establishment_id
            )
        ).order_by(CustomerHistory.created_at.desc()).all()

        # Procesamos la fecha para que incluya el huso horario local
        for r in records:
            if r.created_at:
                # astimezone() añade el offset (ej. -05:00) automáticamente
                r.created_at = r.created_at.astimezone(local_tz)

        return records

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))