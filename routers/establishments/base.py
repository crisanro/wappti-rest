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

import firebase_admin
from firebase_admin import auth


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

        # B. Crear el Establecimiento con su idioma
        new_establishment = Establishment(
            id=establishment_id,
            name=data.name,
            email=establishment_email,
            language=data.language,  # <--- GUARDAMOS EL IDIOMA AQUÍ
            is_suspended=False,
            is_deleted=False,
        )
        db.add(new_establishment)

        # C. Crear el Primer Perfil
        new_profile = Profile(
            name=data.name, 
            establishment_id=establishment_id,
            timezone=data.timezone,
            message_language=""
        )
        db.add(new_profile)

        # D. Registro de Log
        register_action_log(
            db=db,
            establishment_id=establishment_id,
            action="BUSINESS_SETUP_COMPLETED",
            method="POST",
            path=request.url.path,
            payload={
                "name": data.name, 
                "timezone": data.timezone, 
                "language": data.language
            },
            request=request
        )

        # E. Commit y Refresh para obtener los datos generados
        db.commit()
        db.refresh(new_profile)

        # --- RESPUESTA CON LOS 4 VALORES SOLICITADOS ---
        return {
            "establishment_id": establishment_id,
            "establishment_language": new_establishment.language,
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
    
@router.delete("/")
def terminate_establishment_data(
    db: Session = Depends(get_db),
    token_data: dict = Depends(verify_firebase_token)
):
    establishment_id = token_data.get('uid')

    # 1. Verify establishment existence
    establishment = db.query(Establishment).filter(Establishment.id == establishment_id).first()
    if not establishment:
        raise HTTPException(status_code=404, detail="ESTABLISHMENT_NOT_FOUND")

    try:
        # --- PHASE 1: DELETE ALL OPERATIONAL TRASH (PHYSICAL DELETE) ---
        
        # Access & Security
        db.query(AppAccessPin).filter(AppAccessPin.id == establishment_id).delete()
        db.query(WhatsappAuthPin).filter(WhatsappAuthPin.associated_phone.in_(
            db.query(Customer.phone).filter(Customer.establishment_id == establishment_id)
        )).delete()

        # Financial & Planning (CASCADE will handle items/payments)
        db.query(CustomerPlan).filter(CustomerPlan.establishment_id == establishment_id).delete()
        db.query(CustomerDebt).filter(CustomerDebt.establishment_id == establishment_id).delete()
        db.query(CustomerBillingProfile).filter(CustomerBillingProfile.establishment_id == establishment_id).delete()
        
        # History & Feedback
        db.query(CustomerHistory).filter(CustomerHistory.establishment_id == establishment_id).delete()
        db.query(CustomerFeedback).filter(CustomerFeedback.establishment_signature == establishment_id).delete()
        
        # Marketing & System
        db.query(CustomerTag).filter(CustomerTag.establishment_id == establishment_id).delete()
        db.query(CalendarNote).filter(CalendarNote.establishment_id == establishment_id).delete()
        db.query(WhatsappCampaign).filter(WhatsappCampaign.establishment_id == establishment_id).delete()
        db.query(WhatsappDispatch).filter(WhatsappDispatch.establishment_id == establishment_id).delete()
        db.query(PendingFollowup).filter(PendingFollowup.establishment_id == establishment_id).delete()
        db.query(SystemAlert).filter(SystemAlert.establishment_id == establishment_id).delete()

        # Profiles & Audits
        db.query(Profile).filter(Profile.establishment_id == establishment_id).delete()
        db.query(SystemAudit).filter(SystemAudit.establishment_id == establishment_id).delete()
        db.query(UsageAuditLog).filter(UsageAuditLog.establishment_id == establishment_id).delete()

        # --- PHASE 2: SELECTIVE APPOINTMENT CLEANUP ---
        # Keep only records with whatsapp_id (History of service)
        db.query(Appointment).filter(
            Appointment.establishment_id == establishment_id,
            (Appointment.whatsapp_id == None) | (Appointment.whatsapp_id == "")
        ).delete()

        # --- PHASE 3: CUSTOMER ANONIMIZATION & CLEANUP ---
        # We fetch emails before anonymizing if they have Firebase accounts
        customers = db.query(Customer).filter(Customer.establishment_id == establishment_id).all()
        
        for cust in customers:
            # Optional: Delete customer from Firebase if they have an account
            if cust.email:
                try:
                    user_fb = auth.get_user_by_email(cust.email)
                    auth.delete_user(user_fb.uid)
                except:
                    pass # User doesn't exist in Firebase Auth

        # Now anonymize them in our DB
        db.query(Customer).filter(Customer.establishment_id == establishment_id).update({
            "first_name": "deleted_user",
            "last_name": "deleted_user",
            "email": "deleted@deleted.com",
            "phone": 0,
            "identification_id": None,
            "notes": "data_purged_due_to_establishment_closure"
        })

        # --- PHASE 4: SOFT DELETE ESTABLISHMENT & FIREBASE TERMINATION ---
        establishment.is_deleted = True
        
        # Final Blow: Delete the establishment owner from Firebase Auth
        try:
            auth.delete_user(establishment_id)
        except Exception as fe:
            print(f"⚠️ FIREBASE_OWNER_DELETE_ERROR: {fe}")

        # --- PHASE 5: PROTECT YOUR DATA (DO NOT DELETE) ---
        # table 'payments' is NEVER touched here.
        
        db.commit()
        return {"status": "success", "message": "purge_completed_firebase_account_deleted"}

    except Exception as e:
        db.rollback()
        import traceback
        print(f"🚨 CRITICAL_PURGE_ERROR: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail="INTERNAL_SERVER_ERROR_ON_PURGE")