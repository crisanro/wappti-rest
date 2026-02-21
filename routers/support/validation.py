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

from .firestore import update_user_reminders

router = APIRouter() # Quitamos la dependencia global si algunos endpoints son públicos, o la mantenemos si todos requieren token.
ws_service = WhatsAppService()



# --- 2. SEND VERIFICATION PIN ---
@router.post("/request-verification-pin")
async def request_verification_pin(
    data: PinRequestSchema, 
    request: Request,
    db: Session = Depends(get_db), 
    token_data: dict = Depends(verify_firebase_token)
):
    user_id = token_data['uid']
    
    # 1. Buscar registro previo
    record = db.query(WhatsAppAuthPin).filter(WhatsAppAuthPin.id == user_id).first()

    # 2. Lógica de Seguridad y Validación de Número
    if record:
        # --- VALIDACIÓN CRÍTICA: ¿El número es el mismo? ---
        if record.associated_phone != data.phone:
            # Logueamos esto como un intento de manipulación
            register_action_log(
                db, user_id, "PIN_SECURITY_VIOLATION_PHONE_MISMATCH", "POST", 
                request.url.path, 
                {"original": record.associated_phone, "attempted": data.phone}, 
                request
            )
            db.commit()
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, 
                detail="security_violation_phone_mismatch_detected"
            )

        if record.is_activated:
            raise HTTPException(status_code=400, detail="account_already_activated")
        
        if record.send_attempts >= 3:
            register_action_log(db, user_id, "PIN_REJECTED_LIMIT_EXCEEDED", "POST", request.url.path, {"phone": data.phone}, request)
            db.commit()
            raise HTTPException(status_code=429, detail="limit_exceeded_contact_support")
        
        # REENVÍO SEGURO
        current_pin = record.pin
        record.send_attempts += 1
        action_type = f"PIN_RESENT_ATTEMPT_{record.send_attempts}"
    else:
        # PRIMER ENVÍO
        current_pin = random.randint(1000, 9999)
        record = WhatsAppAuthPin(
            id=user_id,
            pin=current_pin,
            is_activated=False,
            send_attempts=1,
            associated_phone=data.phone
        )
        db.add(record)
        action_type = "PIN_FIRST_REQUEST"

    # 3. LOGGING DE ÉXITO O REENVÍO
    register_action_log(
        db, user_id, action_type, "POST", request.url.path, 
        {"phone": data.phone, "attempt": record.send_attempts, "pin": current_pin}, 
        request
    )
    db.commit()

    # 4. ENVÍO WHATSAPP
    try:
        await ws_service._send({
            "messaging_product": "whatsapp",
            "to": str(data.phone),
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
    except Exception as e:
        register_action_log(db, user_id, "PIN_WS_ERROR", "ERROR", request.url.path, {"error": str(e)}, request)
        db.commit()

    return {
        "status": "success",
        "attempts_remaining": 3 - record.send_attempts
    }

