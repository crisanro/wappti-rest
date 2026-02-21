import os
import httpx
import random
import traceback
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

from .firestore import update_user_reminders

# --- CONFIGURACIÓN DE RUTAS ESPECÍFICAS ---
WEBHOOK_URL_AUTH_PIN = os.getenv("WEBHOOK_WHATSAPP_AUTH_PIN")


router = APIRouter() # Quitamos la dependencia global si algunos endpoints son públicos, o la mantenemos si todos requieren token.


# --- 2. SEND VERIFICATION PIN ---
@router.post("/request-verification-pin")
async def request_verification_pin(
    data: PinRequestSchema, 
    request: Request,
    db: Session = Depends(get_db), 
    token_data: dict = Depends(verify_firebase_token)
):
    user_id = token_data['uid']
    
    try:
        # 1. Obtener info del establecimiento (Nombre e Idioma)
        establishment = db.query(Establishment).filter(Establishment.id == user_id).first()
        if not establishment:
            raise HTTPException(status_code=404, detail="ESTABLISHMENT_NOT_FOUND")

        # 2. Buscar si ya existe un PIN para este usuario
        record = db.query(WhatsAppAuthPin).filter(WhatsAppAuthPin.id == user_id).first()

        # 3. Validaciones de Seguridad
        if record:
            # Bloqueo si intenta cambiar el número en este paso (debe usar el endpoint de reset)
            if str(record.associated_phone) != str(data.phone):
                register_action_log(
                    db, user_id, "SECURITY_PIN_PHONE_MISMATCH", "POST", 
                    request.url.path, {"db": record.associated_phone, "req": data.phone}, 
                    request
                )
                db.commit()
                raise HTTPException(status_code=403, detail="security_violation_phone_mismatch")

            if record.is_activated:
                raise HTTPException(status_code=400, detail="account_already_activated")
            
            if record.send_attempts >= 3:
                raise HTTPException(status_code=429, detail="too_many_attempts_contact_support")
            
            current_pin = record.pin
            record.send_attempts += 1
            action_type = f"PIN_RESENT_AT_{record.send_attempts}"
        else:
            # Generación de primer PIN
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

        # 4. Envío al Webhook Específico (Solo para PINs)
        # Enviamos toda la metadata necesaria para la plantilla de WhatsApp
        webhook_payload = {
            "source": "auth_system",
            "establishment_id": user_id,
            "establishment_name": establishment.name or "S/N",
            "phone_to": str(data.phone),
            "pin": str(current_pin),
            "language": establishment.language or "es", # Obtenido de la DB
            "timestamp": datetime.now(timezone.utc).isoformat()
        }

        if WEBHOOK_URL_AUTH_PIN:
            try:
                async with httpx.AsyncClient() as client:
                    # Lo enviamos y no esperamos una respuesta pesada, solo el envío
                    await client.post(WEBHOOK_URL_AUTH_PIN, json=webhook_payload, timeout=5.0)
            except Exception as web_err:
                print(f"⚠️ Error enviando al Webhook de PIN: {str(web_err)}")
                # El proceso sigue aunque el webhook falle (para que el log quede guardado)
        
        # 5. Registro y Finalización
        register_action_log(
            db, user_id, action_type, "POST", request.url.path, 
            {"phone": data.phone, "attempt": record.send_attempts}, request
        )
        db.commit()

        return {
            "status": "success",
            "attempts_remaining": 3 - record.send_attempts
        }

    except HTTPException as he:
        raise he
    except Exception as e:
        db.rollback()
        print(f"🚨 ERROR EN REQUEST-PIN:\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail="internal_server_error")
