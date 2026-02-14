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

@router.get("/", response_model=EstablishmentInfo)
def get_establishment_info(
    db: Session = Depends(get_db), 
    token_data: dict = Depends(verify_firebase_token)
):
    """
    Retrieves information for the establishment linked to the token UID.
    """
    establishment_id = token_data.get('uid')
    establishment = db.query(Establishment).filter(Establishment.id == establishment_id).first()
    
    if not establishment:
        raise HTTPException(status_code=404, detail="Configuration not found")
    
    return establishment


@router.post("/", status_code=201)
def setup_new_business(
    data: SetupEstablishmentRequest,
    request: Request,
    db: Session = Depends(get_db),
    token_data: dict = Depends(verify_firebase_token)
):
    establishment_id = token_data.get('uid')
    establishment_email = token_data.get('email')

    try:
        # A. Verificación de existencia
        existing = db.query(Establishment).filter(Establishment.id == establishment_id).first()
        if existing:
            raise HTTPException(status_code=400, detail="business_already_registered")

        # B. Crear el Establecimiento
        new_establishment = Establishment(
            id=establishment_id,
            name=data.name,
            email=establishment_email,
            is_suspended=False,
            is_deleted=False
        )
        db.add(new_establishment)

        # C. Crear el Primer Perfil
        new_profile = Profile(
            name=data.name, 
            establishment_id=establishment_id,
            timezone=data.timezone,
            message_language="es"
        )
        db.add(new_profile)

        # D. Registro de Log
        register_action_log(
            db=db,
            establishment_id=establishment_id,
            action="BUSINESS_SETUP_COMPLETED",
            method="POST",
            path=request.url.path,
            payload={"name": data.name, "timezone": data.timezone},
            request=request
        )

        # E. Commit y Refresh para obtener los datos generados
        db.commit()
        db.refresh(new_profile)

        # --- RESPUESTA CON LOS 4 VALORES SOLICITADOS ---
        return {
            "establishment_id": establishment_id,
            "profile_id": new_profile.id,
            "profile_name": new_profile.name,
            "profile_timezone": new_profile.timezone
        }

    except Exception as e:
        db.rollback()
        print(f"🚨 SETUP ERROR: {str(e)}")
        raise HTTPException(status_code=500, detail="initial_setup_failed")
    
    

@router.patch("/")
def update_my_business(
    data: EstablishmentUpdate, 
    db: Session = Depends(get_db), 
    token_data: dict = Depends(verify_firebase_token)
):
    # 1. Identificamos al usuario por su Token (Seguridad absoluta)
    uid = token_data.get('uid')
    
    # 2. Buscamos su registro
    business = db.query(Establishment).filter(Establishment.id == uid).first()
    
    if not business:
        raise HTTPException(status_code=404, detail="business_not_found")
    
    # 3. Solo extraemos lo que el Schema permitió
    update_fields = data.model_dump(exclude_unset=True)
    
    if not update_fields:
        raise HTTPException(status_code=400, detail="no_valid_fields_provided")

    # 4. Aplicamos los cambios
    for key, value in update_fields.items():
        setattr(business, key, value)
    
    try:
        db.commit()
        
        # Auditoría para saber qué cambió el usuario
        register_action_log(db, uid, "SELF_UPDATE_BRANDING", "PATCH", "/business/update", update_fields)
        
        return {
            "status": "success", 
            "message": "Branding actualizado correctamente",
            "changes": list(update_fields.keys())
        }
    except Exception as e:
        db.rollback()
        print(f"🚨 Error en Update: {str(e)}")
        raise HTTPException(status_code=500, detail="error_processing_update")
    


@router.get("/recent-stats")
def get_recent_stats(
    tz_name: str = "America/Guayaquil",
    db: Session = Depends(get_db),
    token_data: dict = Depends(verify_firebase_token)
):
    """
    Estadísticas de los últimos 7 días:
    - Citas con status 'Attended'
    - Cuántas de esas tienen whatsapp_id (mensajes enviados)
    """
    establishment_id = token_data.get('uid')

    try:
        # 1. Configurar rango de tiempo (7 días atrás desde hoy)
        try:
            local_tz = pytz.timezone(tz_name)
        except:
            local_tz = pytz.UTC

        now_local = datetime.now(local_tz)
        # Siete días atrás desde el inicio de hoy
        seven_days_ago_local = (now_local - timedelta(days=7)).replace(hour=0, minute=0, second=0)
        
        # Convertir a UTC para la base de datos
        start_utc = seven_days_ago_local.astimezone(pytz.UTC)

        # 2. Consulta de Citas Atendidas
        # Filtramos por establecimiento, fecha y que el texto de respuesta sea exactamente 'Attended'
        attended_appointments = db.query(Appointment).filter(
            and_(
                Appointment.establishment_id == establishment_id,
                Appointment.appointment_date >= start_utc,
                Appointment.response_text == 'Attended'
            )
        ).all()

        total_attended = len(attended_appointments)
        
        # 3. Contar cuántas tienen whatsapp_id (Mensajes automáticos enviados)
        messages_sent = len([a for a in attended_appointments if a.whatsapp_id is not None])

        # 4. Calcular un porcentaje de cobertura (opcional, para que se vea pro en la UI)
        coverage_rate = (messages_sent / total_attended * 100) if total_attended > 0 else 0

        return {
            "period_days": 7,
            "total_attended_appointments": total_attended,
            "total_messages_sent": messages_sent,
            "coverage_percentage": round(coverage_rate, 2),
            "label": "Métricas de fidelización (Últimos 7 días)"
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