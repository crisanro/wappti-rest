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
    try:
        establishment_id = token_data.get('uid')
        local_tz = pytz.timezone(timezone_name)

        # 1. Ajuste de límites: esto asegura que el registro de las 11:55 PM aparezca
        def get_utc_boundary(date_str, is_end=False):
            dt = datetime.strptime(date_str[:10], "%Y-%m-%d")
            if is_end:
                # Vamos hasta el último microsegundo del día del usuario
                dt = dt.replace(hour=23, minute=59, second=59, microsecond=999999)
            else:
                dt = dt.replace(hour=0, minute=0, second=0, microsecond=0)
            
            # Localizamos en Guayaquil y pasamos a UTC para la consulta
            return local_tz.localize(dt).astimezone(pytz.UTC)

        start_dt_utc = get_utc_boundary(start_date)
        end_dt_utc = get_utc_boundary(end_date, is_end=True)

        # 2. Consulta
        records = db.query(CustomerHistory).filter(
            and_(
                CustomerHistory.establishment_id == establishment_id,
                CustomerHistory.created_at >= start_dt_utc,
                CustomerHistory.created_at <= end_dt_utc
            )
        ).order_by(CustomerHistory.created_at.desc()).all()

        # 3. Respuesta plana con nombres originales y 'notes'
        return [
            {
                "created_at": r.created_at.astimezone(local_tz).isoformat(),
                "process_name": r.process_name,
                "income": float(r.income) if r.income else 0.0,
                "profile_id": r.profile_id,
                "customer_id": r.customer_id,
                "notes": r.notes  # <--- Agregado
            }
            for r in records
        ]

    except Exception as e:
        print(f"Error: {e}")
        raise HTTPException(status_code=500, detail="Error al obtener historial")


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