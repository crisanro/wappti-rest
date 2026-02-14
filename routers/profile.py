from fastapi import APIRouter, Depends, HTTPException, status, Query
from fastapi.encoders import jsonable_encoder
from datetime import datetime, timezone, date
from sqlalchemy.sql import func
from sqlalchemy.orm import Session
from sqlalchemy import and_, cast, Date

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
    CalendarNoteCreate
)

from pydantic import ValidationError
from traceback import print_exc
# Apply global security to the business router
router = APIRouter(dependencies=[Depends(verify_firebase_token)])


# --- SECTION: PROFILES (STAFF) ---
@router.get("/", response_model=list[ProfileResponse])
def list_staff_profiles(
    db: Session = Depends(get_db), 
    token_data: dict = Depends(verify_firebase_token)
):
    establishment_id = token_data.get('uid')
    
    # Consultamos la base de datos
    profiles = db.query(Profile).filter(Profile.establishment_id == establishment_id).all()
    
    # Al tener el response_model arriba, FastAPI filtrará el establishment_id automáticamente
    return profiles
    

@router.post("/", response_model=ProfileResponse)
def create_profile(
    data: ProfileCreate, 
    db: Session = Depends(get_db), 
    token_data: dict = Depends(verify_firebase_token)
):
    e_id = token_data.get('uid')
    
    # 1. Normalizar nombre (quitar espacios extra)
    clean_name = data.name.strip()

    # 2. Verificar si el nombre ya existe para ESTE establecimiento
    duplicate = db.query(Profile).filter(
        and_(
            Profile.establishment_id == e_id,
            Profile.name.ilike(clean_name) # ilike para evitar "Juan" vs "juan"
        )
    ).first()

    if duplicate:
        raise HTTPException(
            status_code=400, 
            detail="profile_name_already_exists"
        )

    # 3. Crear el nuevo perfil
    try:
        new_profile = Profile(
            name=clean_name,
            timezone=data.timezone,
            message_language=data.message_language,
            extra_data_1=data.extra_data_1,
            extra_data_2=data.extra_data_2,
            establishment_id=e_id
        )
        
        db.add(new_profile)
        db.commit()
        db.refresh(new_profile)
        return new_profile

    except Exception as e:
        db.rollback()
        print(f"🚨 Error al crear perfil: {str(e)}")
        raise HTTPException(status_code=500, detail="error_creating_profile")
    


# --- ACTUALIZAR PERFIL (PATCH) ---
@router.patch("/{profile_id}")
def update_staff_profile(
    profile_id: int, 
    data: ProfileUpdate, 
    db: Session = Depends(get_db),
    token_data: dict = Depends(verify_firebase_token)
):
    e_id = token_data.get('uid')
    
    # 1. Buscar el perfil original
    profile = db.query(Profile).filter(
        and_(Profile.id == profile_id, Profile.establishment_id == e_id)
    ).first()
    
    if not profile:
        raise HTTPException(status_code=404, detail="profile_not_found")

    # 2. Si el usuario está intentando cambiar el nombre, validar duplicados
    if data.name and data.name != profile.name:
        name_conflict = db.query(Profile).filter(
            and_(
                Profile.establishment_id == e_id,
                Profile.name == data.name,
                Profile.id != profile_id  # Que no sea el mismo perfil que estamos editando
            )
        ).first()
        
        if name_conflict:
            raise HTTPException(
                status_code=400, 
                detail="profile_name_already_exists"
            )

    # 3. Aplicar cambios
    update_dict = data.model_dump(exclude_unset=True)
    for key, value in update_dict.items():
        setattr(profile, key, value)
    
    try:
        db.commit()
        db.refresh(profile)
        return {"status": "success", "message": "profile_updated"}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail="error_updating_profile")