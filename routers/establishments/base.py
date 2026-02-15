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
    
