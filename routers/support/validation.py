import os
import httpx
import random
import traceback
from fastapi import APIRouter, Depends, HTTPException, status, Query, Request
from sqlalchemy.orm import Session
from datetime import datetime, timezone

from core.database import get_db
from core.auth import verify_firebase_token
from core.utils import register_action_log

# Import de modelos y esquemas
from models import *
from schemas.validation import PinRequestSchema

# --- CONFIGURACIÓN ---
# Asegúrate de que esta variable esté en tu .env o entorno de Docker
WEBHOOK_URL_AUTH_PIN = os.getenv("WEBHOOK_WHATSAPP_AUTH_PIN")

router = APIRouter()

@router.post("/request-verification-pin")
async def request_verification_pin(
    data: PinRequestSchema, 
    request: Request,
    db: Session = Depends(get_db), 
    token_data: dict = Depends(verify_firebase_token)
):
    user_id = token_data['uid']
    
    try:
        # 1. Obtener info del establecimiento
        establishment = db.query(Establishment).filter(Establishment.id == user_id).first()
        if not establishment:
            raise HTTPException(status_code=404, detail="ESTABLISHMENT_NOT_FOUND")

        # 2. Gestión de registros de PIN
        record = db.query(WhatsAppAuthPin).filter(WhatsAppAuthPin.id == user_id).first()

        if record:
            if str(record.associated_phone) != str(data.phone):
                # Log de seguridad antes de lanzar la excepción
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

        # --- 3. ENVÍO AL WEBHOOK (WhatsApp Service) ---
        
        webhook_payload = {
            "source": "auth_system",
            "establishment_id": str(user_id),
            "establishment_name": str(establishment.name or "S/N"),
            "phone_to": str(data.phone),
            "pin": str(current_pin),
            "language": str(establishment.language or "es"),
            "timestamp": datetime.now(timezone.utc).isoformat()
        }

        # Verificación técnica en consola
        if not WEBHOOK_URL_AUTH_PIN:
            print("⚠️ ADVERTENCIA: WEBHOOK_URL_AUTH_PIN no está definida. El PIN no se enviará.")
        else:
            try:
                # Usamos un bloque asíncrono con un timeout más robusto
                async with httpx.AsyncClient(timeout=10.0) as client:
                    response = await client.post(WEBHOOK_URL_AUTH_PIN, json=webhook_payload)
                    
                    # Log para debug en consola si falla el destino
                    if response.status_code not in [200, 201]:
                        print(f"❌ Webhook Error {response.status_code}: {response.text}")
            except Exception as web_err:
                print(f"🚨 Fallo de conexión con Webhook: {str(web_err)}")

        # 4. Finalización de la transacción SQL
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
