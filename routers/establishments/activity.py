from fastapi import APIRouter, Depends, HTTPException, status, Query, Request
from fastapi.encoders import jsonable_encoder
from datetime import datetime, timezone, date, timedelta
from sqlalchemy.sql import func
from sqlalchemy.orm import Session
from sqlalchemy import and_, cast, Date
import pytz
from core.database import get_db
from core.auth import verify_firebase_token
from core.utils import register_action_log

# Import English models
from models import *

# Import updated schemas
from schemas.business import (
    EstablishmentInfo, 
    EstablishmentUpdate, 
    PinUpdate, 
    ProfileResponse,
    ProfileCreate,
    ProfileBase, 
    ProfileUpdate,
    TutorialLinkResponse,
    CalendarNoteResponse,
    CalendarNoteCreate,
    SetupEstablishmentRequest
)

from pydantic import ValidationError
from traceback import print_exc
# Apply global security to the business router
router = APIRouter(dependencies=[Depends(verify_firebase_token)])


@router.get("/recent-stats")
def get_recent_stats(
    tz_name: str = "America/Guayaquil",
    db: Session = Depends(get_db),
    token_data: dict = Depends(verify_firebase_token)
):

    establishment_id = token_data.get('uid')

    if not establishment_id:
        raise HTTPException(status_code=401, detail="Invalid token: UID missing")

    try:
        # 1. Configuración de tiempo
        try:
            local_tz = pytz.timezone(tz_name)
        except:
            local_tz = pytz.UTC

        now_local = datetime.now(local_tz)
        seven_days_ago_local = (now_local - timedelta(days=7)).replace(hour=0, minute=0, second=0)
        start_utc = seven_days_ago_local.astimezone(pytz.UTC)

        # 2. Consulta Base: Solo registros que pertenecen al establecimiento Y tienen whatsapp_id
        # Esto garantiza que no procesamos información ajena
        base_query = db.query(Appointment).filter(
            and_(
                Appointment.establishment_id == establishment_id, # <--- CANDADO DE SEGURIDAD
                Appointment.appointment_date >= start_utc,
                Appointment.whatsapp_id.isnot(None) # Solo los que tienen ID de mensaje
            )
        ).all()

        # 3. Cálculo de métricas sobre el set seguro de datos
        messages_sent = len(base_query)
        
        # Filtramos de los mensajes enviados, cuáles fueron atendidos
        attended_from_messages = [a for a in base_query if a.response_text == 'Attended']
        total_attended = len(attended_from_messages)

        # 4. Porcentaje de efectividad
        coverage_rate = (total_attended / messages_sent * 100) if messages_sent > 0 else 0

        return {
            "period_days": 7,
            "total_messages_sent": messages_sent,
            "total_attended_appointments": total_attended,
            "conversion_rate": round(coverage_rate, 2),
            "owner_id": establishment_id, # Confirmación opcional para el front
            "label": "Efectividad de Mensajería (7 días)"
        }

    except Exception as e:
        import traceback
        print(f"🚨 ERROR CRÍTICO EN STATS: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail="error_processing_stats")
        

@router.get("/pin")
def verify_access_pin(
    pin: str, 
    db: Session = Depends(get_db), 
    token_data: dict = Depends(verify_firebase_token)
):
    """
    Verifies the 6-digit PIN for Assistant Mode.
    """
    establishment_id = token_data.get('uid')
    
    access = db.query(AppAccessPin).filter(
        and_(AppAccessPin.id == establishment_id, AppAccessPin.pin == pin)
    ).first()
    
    if not access:
        raise HTTPException(status_code=401, detail="invalid_pin")
    
    return {"access_granted": True}


@router.patch("/pin")
def update_access_pin(
    data: PinUpdate, 
    db: Session = Depends(get_db), 
    token_data: dict = Depends(verify_firebase_token)
):
    """
    Updates or initializes the 6-digit security PIN.
    """
    establishment_id = token_data.get('uid')
    record = db.query(AppAccessPin).filter(AppAccessPin.id == establishment_id).first()
    
    if not record:
        record = AppAccessPin(id=establishment_id, pin=data.pin)
        db.add(record)
    else:
        record.pin = data.pin
    
    try:
        db.commit()
        register_action_log(db, establishment_id, "UPDATE_PIN", "PATCH", "/business/security/pin-update", {"pin": "****"})
        return {"status": "success", "message": "Security PIN updated successfully"}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail="Error saving security PIN")
