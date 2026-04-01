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
    """
    Estadísticas actualizadas:
    - total_messages_sent: Citas que tienen un whatsapp_id (sin importar el status).
    - total_attended_appointments: Citas del grupo anterior que tienen status 'Attended'.
    """
    establishment_id = token_data.get('uid')

    try:
        # 1. Configurar rango de tiempo (UTC)
        try:
            local_tz = pytz.timezone(tz_name)
        except:
            local_tz = pytz.UTC

        now_local = datetime.now(local_tz)
        seven_days_ago_local = (now_local - timedelta(days=7)).replace(hour=0, minute=0, second=0)
        start_utc = seven_days_ago_local.astimezone(pytz.UTC)

        # 2. Obtener todas las citas con whatsapp_id (Mensajes enviados)
        # Filtramos por establecimiento, fecha y que whatsapp_id NO sea nulo
        appointments_with_whatsapp = db.query(Appointment).filter(
            and_(
                Appointment.establishment_id == establishment_id,
                Appointment.appointment_date >= start_utc,
                Appointment.whatsapp_id != None  # IMPORTANTE: Que tenga ID de WhatsApp
            )
        ).all()

        messages_sent = len(appointments_with_whatsapp)

        # 3. De esos mensajes enviados, filtrar los que fueron atendidos
        # Usamos una lista de comprensión sobre el resultado anterior
        attended_from_messages = [a for a in appointments_with_whatsapp if a.response_text == 'Attended']
        total_attended = len(attended_from_messages)

        # 4. Calcular porcentaje de conversión/asistencia
        # ¿Qué porcentaje de los mensajes enviados terminaron en una cita asistida?
        coverage_rate = (total_attended / messages_sent * 100) if messages_sent > 0 else 0

        return {
            "period_days": 7,
            "total_messages_sent": messages_sent,
            "total_attended_appointments": total_attended,
            "coverage_percentage": round(coverage_rate, 2),
            "label": "Métricas de conversión de mensajes (Últimos 7 días)"
        }

    except Exception as e:
        import traceback
        print(f"🚨 ERROR EN STATS: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail="error_fetching_recent_stats")
    

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
