import os
import httpx
import random
import traceback
from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy.orm import Session
from datetime import datetime, timezone

from core.database import get_db
from core.auth import verify_firebase_token
from core.utils import register_action_log

# Import Models & Schemas
from models import Establishment, WhatsAppAuthPin
from schemas.validation import PinRequestSchema

# Configuration
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
        # 1. Fetch Establishment Info (Name and Language)
        establishment = db.query(Establishment).filter(Establishment.id == user_id).first()
        if not establishment:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, 
                detail="ESTABLISHMENT_NOT_FOUND"
            )

        # 2. Check if a PIN record already exists for this user
        record = db.query(WhatsAppAuthPin).filter(WhatsAppAuthPin.id == user_id).first()

        # 3. Security Validations
        if record:
            # Block if attempting to change the phone number here (must use reset endpoint)
            if str(record.associated_phone) != str(data.phone):
                register_action_log(
                    db, user_id, "SECURITY_PIN_PHONE_MISMATCH", "POST", 
                    request.url.path, {"db": record.associated_phone, "req": data.phone}, 
                    request
                )
                db.commit()
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN, 
                    detail="security_violation_phone_mismatch"
                )

            if record.is_activated:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST, 
                    detail="account_already_activated"
                )
            
            if record.send_attempts >= 3:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS, 
                    detail="too_many_attempts_contact_support"
                )
            
            current_pin = record.pin
            record.send_attempts += 1
            action_type = f"PIN_RESENT_AT_{record.send_attempts}"
        else:
            # Generate the first PIN
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

        # 4. Prepare Webhook Payload
        webhook_payload = {
            "source": "auth_system",
            "establishment_id": str(user_id),
            "establishment_name": establishment.name or "Unknown",
            "phone_to": str(data.phone),
            "pin": str(current_pin),
            "language": establishment.language or "en",
            "timestamp": datetime.now(timezone.utc).isoformat()
        }

        # 5. Execute Webhook Call
        if WEBHOOK_URL_AUTH_PIN:
            try:
                # Using a 10s timeout to account for external API latency
                async with httpx.AsyncClient(timeout=10.0) as client:
                    response = await client.post(WEBHOOK_URL_AUTH_PIN, json=webhook_payload)
                    # Raise exception for 4xx or 5xx responses to catch them in the log
                    response.raise_for_status()
            except httpx.HTTPStatusError as http_err:
                print(f"❌ Webhook returned error: {http_err.response.status_code} - {http_err.response.text}")
            except Exception as web_err:
                print(f"⚠️ Connection error sending PIN Webhook: {str(web_err)}")
        else:
            print("🚫 WEBHOOK_URL_AUTH_PIN is not configured in environment variables")
        
        # 6. Logging and Finalization
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
        print(f"🚨 ERROR IN REQUEST-PIN:\n{traceback.format_exc()}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
            detail="internal_server_error"
        )
