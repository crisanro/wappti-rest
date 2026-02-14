import random
from fastapi import APIRouter, Depends, HTTPException, status, Query, Request
from sqlalchemy.orm import Session
from sqlalchemy import and_, cast, Date
from firebase_admin import firestore
from datetime import datetime, timezone

from core.database import get_db
from core.auth import verify_firebase_token
from core.utils import register_action_log

# Import English models & Schemas
from models import *
from schemas.validation import CheckPhoneSchema, PinRequestSchema, VerifyPinSchema, LinkReferralRequest
from services.whatsapp_service import WhatsAppService

router = APIRouter() # Quitamos la dependencia global si algunos endpoints son públicos, o la mantenemos si todos requieren token.
ws_service = WhatsAppService()

# --- 1. CHECK UNIQUE PHONE ---
@router.post("/check-unique-phone")
async def check_unique_phone(data: CheckPhoneSchema):
    """Checks Firestore to ensure the phone number is not registered."""
    db_fs = firestore.client()
    # Limitar a 1 para optimizar la consulta
    docs = db_fs.collection("users").where("phone_number", "==", data.phone).limit(1).stream()
    exists = any(docs)
    return {"status": not exists}

# --- 2. SEND VERIFICATION PIN ---
@router.post("/request-verification-pin")
async def request_verification_pin(
    data: PinRequestSchema, 
    request: Request, # Agregado para seguridad IP
    db: Session = Depends(get_db), 
    token_data: dict = Depends(verify_firebase_token)
):
    user_id = token_data['uid']
    record = db.query(WhatsAppAuthPin).filter(WhatsAppAuthPin.id == user_id).first()

    if record and (record.is_active or record.send_attempts >= 3):
        raise HTTPException(
            status_code=400, 
            detail="limit_exceeded_contact_support"
        )
    
    current_pin = record.pin if record else random.randint(1000, 9999)

    if record:
        record.send_attempts += 1
    else:
        record = WhatsAppAuthPin(
            id=user_id,
            pin=current_pin,
            is_active=False,
            send_attempts=1,
            associated_phone=data.phone
        )
        db.add(record)

    # Log attempt (Security & Heartbeat)
    register_action_log(db, user_id, "PIN_REQUESTED", "POST", request.url.path, {"phone": data.phone}, request)
    db.commit()

    # WhatsApp Dispatch
    await ws_service._send({
        "messaging_product": "whatsapp",
        "to": data.phone,
        "type": "template",
        "template": {
            "name": "solicitud_en_wappti_1519",
            "language": {"code": "es_EC"},
            "components": [{
                "type": "body",
                "parameters": [
                    {"type": "text", "text": data.name},
                    {"type": "text", "text": str(current_pin)}
                ]
            }]
        }
    })
    
    return {"complete": True, "message": "Verification code sent"}

# --- 3. VALIDATE PIN AND ACTIVATE ---
@router.post("/validate-and-activate")
async def validate_and_activate(
    data: VerifyPinSchema, 
    request: Request,
    db: Session = Depends(get_db), 
    token_data: dict = Depends(verify_firebase_token)
):
    user_id = token_data['uid']
    record = db.query(WhatsAppAuthPin).filter(WhatsAppAuthPin.id == user_id).first()

    if not record:
        raise HTTPException(status_code=404, detail="record_not_found")

    if record.is_active or len(record.validation_attempts or []) >= 3:
        raise HTTPException(status_code=400, detail="account_already_active_or_blocked")

    # PIN Verification
    if record.pin != data.pin:
        attempts = list(record.validation_attempts or [])
        attempts.append(data.pin)
        record.validation_attempts = attempts
        db.commit()
        return {"complete": False, "message": f"invalid_code_{len(attempts)}_3"}

    # Activation Logic
    reward_amount = 15 if data.referred_by else 5
    
    try:
        # A. Firestore Update
        db_fs = firestore.client()
        db_fs.collection("users").document(user_id).update({
            "phone_validate": True,
            "reminders_count": reward_amount
        })

        # B. Audit & Rewards
        db.add(UsageAuditLog(
            establishment_id=user_id,
            observations=f"Welcome bonus {'ref ' + str(data.referred_by) if data.referred_by else ''}",
            value=reward_amount,
            condition="Top-up"
        ))

        if data.referred_by:
            db.query(ReferralCode).filter(ReferralCode.id == data.referred_by).update({"total_users": ReferralCode.total_users + 1})

        # C. Establishment Sync
        db.query(Establishment).filter(Establishment.id == user_id).update({
            "country": data.country,
            "whatsapp": record.associated_phone
        })
        
        record.is_active = True
        
        register_action_log(db, user_id, "ACCOUNT_ACTIVATION", "POST", request.url.path, {"reward": reward_amount}, request)
        db.commit()
        return {"complete": True}

    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"activation_failed: {str(e)}")

# --- 4. LINK REFERRAL CODE ---
@router.post("/link-referral")
def link_referral_code(
    data: LinkReferralRequest, 
    request: Request,
    db: Session = Depends(get_db),
    token_data: dict = Depends(verify_firebase_token)
):
    try:
        establishment_id = token_data.get('uid')
        clean_code = "".join(data.code_text.split()).lower()

        # 1. Buscamos el establecimiento
        establishment = db.query(Establishment).filter(Establishment.id == establishment_id).first()
        
        if not establishment:
            raise HTTPException(status_code=404, detail="establishment_not_found")
        
        if establishment.referred_by:
            raise HTTPException(status_code=400, detail="already_referred")

        # 2. Buscamos el código
        referral_record = db.query(ReferralCode).filter(ReferralCode.code == clean_code).first()
        
        if not referral_record:
            raise HTTPException(status_code=404, detail="invalid_code")

        # 3. Vinculación y actualización del contador
        establishment.referred_by = referral_record.id
        
        if referral_record.user_count is None:
            referral_record.user_count = 1
        else:
            referral_record.user_count += 1

        # 4. Registro de Log
        register_action_log(
            db=db, 
            establishment_id=establishment_id, 
            action="REFERRAL_LINKED", 
            method="POST", 
            path=request.url.path, 
            payload={"code": clean_code, "owner_id": referral_record.id}, 
            request=request
        )
        
        db.commit()

        # --- RESPUESTA CON EL ID DEL DUEÑO ---
        return {
            "status": "success",
            "linked_code": clean_code,
            "referral_id": referral_record.id  # Este es el ID del dueño del código
        }
        
    except HTTPException as he:
        raise he
    except Exception as e:
        db.rollback()
        print(f"🚨 ERROR: {str(e)}")
        raise HTTPException(status_code=500, detail="internal_server_error")