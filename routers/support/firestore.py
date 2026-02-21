import os
from google.cloud import firestore
from google.oauth2 import service_account
from google.cloud.firestore_v1.base_query import FieldFilter
from dotenv import load_dotenv
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from core.auth import verify_firebase_token
from core.database import get_db
from sqlalchemy.orm import Session

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
import httpx

load_dotenv()


async def fire_security_webhook(event_type: str, user_id: str, details: dict, request: Request):
    webhook_url = os.getenv("SECURITY_WEBHOOK_URL")
    if not webhook_url:
        return

    payload = {
        "event": event_type,
        "user_id": user_id,
        "timestamp": datetime.utcnow().isoformat(),
        "ip_address": request.client.host,
        "details": details
    }
    try:
        async with httpx.AsyncClient() as client:
            await client.post(webhook_url, json=payload, timeout=3.0)
    except Exception as e:
        print(f"🚨 Webhook error: {e}")


def update_user_reminders(user_id: str, amount: int):
    """
    Actualiza los recordatorios en Firestore.
    """
    try:
        user_ref = db_firestore.collection("users").document(user_id)
        # Usamos Increment para evitar problemas de lectura/escritura concurrente
        user_ref.update({
            "phone_validate": True,
            "Recordatorios": firestore.Increment(amount)
        })
        return True
    except Exception as e:
        print(f"🚨 Firestore Update Error: {e}")
        return False


def get_firestore_client():
    # 1. Extraer las variables del .env
    project_id = os.getenv("FIREBASE_PROJECT_ID")
    private_key = os.getenv("FIREBASE_PRIVATE_KEY")
    client_email = os.getenv("FIREBASE_CLIENT_EMAIL")
    # private_key_id no es estrictamente necesario para la conexión, 
    # pero Google lo acepta si lo quieres incluir.

    if not all([project_id, private_key, client_email]):
        print("❌ Error: Missing Firebase variables in .env")
        return None

    # 2. IMPORTANTE: Limpiar la llave privada
    # Las llaves RSA vienen con "\n". Si en tu .env las pusiste como texto,
    # Python las lee como caracteres literales y hay que convertirlas a saltos de línea reales.
    if "\\n" in private_key:
        private_key = private_key.replace("\\n", "\n")

    # 3. Crear el diccionario de credenciales
    info = {
        "type": "service_account",
        "project_id": project_id,
        "private_key": private_key,
        "client_email": client_email,
        "token_uri": "https://oauth2.googleapis.com/token",
    }

    try:
        # 4. Crear las credenciales de Service Account
        credentials = service_account.Credentials.from_service_account_info(info)
        return firestore.Client(credentials=credentials, project=project_id)
    except Exception as e:
        print(f"❌ Failed to connect to Firestore: {e}")
        return None

# Inicializamos el cliente
db_firestore = get_firestore_client()
# Inicializamos el cliente de Firestore

router = APIRouter(dependencies=[Depends(verify_firebase_token)])

@router.get("/check-phone")
async def check_firestore_phone(
    phone: str = Query(..., description="Phone to validate"),
    db: Session = Depends(get_db),
    token_data: dict = Depends(verify_firebase_token)
):
    establishment_id = token_data.get('uid')
    
    try:
        # 1. Consultar Firestore
        users_ref = db_firestore.collection("users")
        query = users_ref.where("phone_number", "==", phone).limit(1).get()
        
        is_unique = len(query) == 0
        status_msg = "SUCCESS_UNIQUE" if is_unique else "REJECTED_DUPLICATE"

        # 2. CREAR EL USER LOG (Auditoría en tu SQL)
        # Esto guarda rastro eterno de que este local consultó este número
        new_log = SystemAudit(
            establishment_id=establishment_id,
            action="PHONE_UNIQUENESS_CHECK",
            method="GET",
            path="/check-firestore-phone",
            payload={"phone_queried": phone, "result": status_msg},
            status_code=200 if is_unique else 400,
            created_at=datetime.utcnow()
        )
        db.add(new_log)
        db.commit()

        # 3. Respuesta Final
        if not is_unique:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="PHONE_NUMBER_ALREADY_REGISTERED"
            )

        return {
            "status": "available",
            "message": "phone_number_is_unique",
            "request_logged": True
        }

    except HTTPException as he:
        raise he
    except Exception as e:
        db.rollback()
        print(f"🚨 LOGGING_OR_FIRESTORE_ERROR: {e}")
        raise HTTPException(status_code=500, detail="INTERNAL_SERVER_ERROR")


@router.post("/validate-and-activate", status_code=status.HTTP_200_OK)
async def validate_and_activate(
    data: VerifyPinSchema, 
    request: Request,
    db: Session = Depends(get_db), 
    token_data: dict = Depends(verify_firebase_token)
):
    user_id = str(token_data.get('uid'))
    
    # 1. Fetch Records (Core Validation)
    record = db.query(WhatsAppAuthPin).filter(WhatsAppAuthPin.id == user_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="RECORD_NOT_FOUND")
    
    if record.is_activated:
        raise HTTPException(status_code=400, detail="ALREADY_ACTIVATED")

    # 2. Security Checks (Fail Fast)
    if record.associated_phone != data.phone:
        await fire_security_webhook("PHONE_MISMATCH", user_id, {"got": data.phone}, request)
        raise HTTPException(status_code=403, detail="SECURITY_PHONE_MISMATCH")

    # 3. Referral Validation (Optimized: Reusable Object)
    ref_record = None
    if data.referred_by:
        ref_record = db.query(ReferralCode).filter(ReferralCode.code == data.referred_by).first()
        
        if not ref_record:
            await fire_security_webhook("INVALID_REF_CODE", user_id, {"code": data.referred_by}, request)
            raise HTTPException(status_code=403, detail="INVALID_REFERRAL_CODE")
        
        # Security duplicate check
        if user_id in (ref_record.users_list or []):
            await fire_security_webhook("DUPLICATE_REF_CLAIM", user_id, {"ref_id": ref_record.id}, request)
            raise HTTPException(status_code=403, detail="REFERRAL_ALREADY_CLAIMED")

    # 4. PIN & Attempts Logic
    attempts = list(record.validation_attempts or [])
    if len(attempts) >= 3:
        raise HTTPException(status_code=429, detail="TOO_MANY_ATTEMPTS")

    if record.pin != data.pin:
        attempts.append(data.pin)
        record.validation_attempts = attempts
        db.commit() # Save attempt even if it fails
        raise HTTPException(status_code=400, detail={"msg": "INVALID_PIN", "attempts": len(attempts)})

    # --- ACTIVATION BLOCK (Transactional) ---
    try:
        reward = 20 if ref_record else 10
        ref_id = ref_record.id if ref_record else None
        
        # A. External Sync (Firestore)
        if not update_user_reminders(user_id, reward):
            raise Exception("FIRESTORE_SYNC_FAILED")

        # B. SQL: Update Referral Table (Atomic Array Update)
        if ref_record:
            ref_record.user_count += 1
            # Assignment creates a new list object so SQLAlchemy tracks the change
            new_users_list = list(ref_record.users_list or [])
            new_users_list.append(user_id)
            ref_record.users_list = new_users_list

        # C. SQL: Update Establishment (Bulk Update pattern)
        db.query(Establishment).filter(Establishment.id == user_id).update({
            "country": data.country,
            "whatsapp": str(data.phone),
            "created_at": datetime.utcnow(),
            "referred_by": ref_id, # Internal ID
            "available_credits": Establishment.available_credits + reward,
            "is_suspended": False
        }, synchronize_session=False)

        # D. SQL: Audit Logging
        db.add(UsageAuditLog(
            establishment_id=user_id, 
            condition="top-up", 
            value=reward, 
            observations=f"Welcome bonus{' (Ref: ' + ref_id + ')' if ref_id else ''}"
        ))
        
        # E. Finalize PIN
        record.is_activated = True
        
        db.commit()
        return {"complete": True, "reward_applied": reward}

    except Exception as e:
        db.rollback()
        await fire_security_webhook("ACTIVATION_ERROR", user_id, {"error": str(e)}, request)
        raise HTTPException(status_code=500, detail=f"INTERNAL_SERVER_ERROR: {str(e)}")

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
        # Normalizamos el código (quitamos espacios y pasamos a minúsculas)
        clean_code = "".join(data.code_text.split()).lower()

        # 1. Validación de existencia del negocio en SQL
        establishment = db.query(Establishment).filter(Establishment.id == establishment_id).first()
        
        if not establishment:
            raise HTTPException(status_code=404, detail="establishment_not_found")
        
        if establishment.referred_by:
            raise HTTPException(status_code=400, detail="already_referred")

        # 2. Búsqueda del código de referido
        referral_record = db.query(ReferralCode).filter(ReferralCode.code == clean_code).first()
        
        if not referral_record:
            raise HTTPException(status_code=404, detail="invalid_code")

        # --- LÓGICA DE ACTUALIZACIÓN ---

        # 3. Vincular en SQL y actualizar contador
        establishment.referred_by = referral_record.id
        
        if referral_record.user_count is None:
            referral_record.user_count = 1
        else:
            referral_record.user_count += 1

        # 4. 🔥 ACTUALIZACIÓN EN FIRESTORE 🔥
        # Actualizamos la columna 'Referido' con el ID del dueño del código
        try:
            if db_firestore:
                user_ref = db_firestore.collection("users").document(establishment_id)
                user_ref.update({
                    "Referido": referral_record.id,
                    "referral_code_used": clean_code, # Opcional: para saber qué código usó
                    "updated_at": datetime.now(timezone.utc)
                })
        except Exception as fs_error:
            # Logueamos el error pero no detenemos el proceso SQL 
            # para no arruinar la experiencia del usuario si Firestore tiene lag
            print(f"⚠️ Firestore sync error (Referral): {fs_error}")

        # 5. Registro de Log de Auditoría
        register_action_log(
            db=db, 
            establishment_id=establishment_id, 
            action="REFERRAL_LINKED", 
            method="POST", 
            path=request.url.path, 
            payload={"code": clean_code, "referral_id": referral_record.id}, 
            request=request
        )
        
        db.commit()

        return {
            "status": "success",
            "linked_code": clean_code,
            "referral_id": referral_record.id 
        }
        
    except HTTPException as he:
        raise he
    except Exception as e:
        db.rollback()
        print(f"🚨 REFERRAL ERROR: {str(e)}")
        raise HTTPException(status_code=500, detail="internal_server_error")

@router.post("/reset-registration-phone")
async def reset_registration_phone(
    request: Request,
    db: Session = Depends(get_db),
    token_data: dict = Depends(verify_firebase_token)
):
    """
    Permite al usuario resetear su número de teléfono de registro por única vez.
    Limpia SQL (whatsapp_auth_pins) y resetea Firestore (users).
    """
    user_id = str(token_data.get('uid'))

    try:
        # 1. Verificar en Firestore si ya usó su oportunidad
        user_ref = db_firestore.collection("users").document(user_id)
        user_doc = user_ref.get()

        if not user_doc.exists:
            raise HTTPException(status_code=404, detail="USER_NOT_FOUND")

        user_data = user_doc.to_dict()
        
        # Comprobar el "candado" de cambio único
        if user_data.get("CambNumRegistro") is True:
            raise HTTPException(
                status_code=403, 
                detail="PHONE_CHANGE_ALREADY_USED"
            )

        # 2. Operación en Postgres: Eliminar el PIN pendiente
        # Esto permite que el número viejo quede "libre" o simplemente se descarte el proceso actual
        pin_record = db.query(WhatsAppAuthPin).filter(WhatsAppAuthPin.id == user_id).first()
        if pin_record:
            db.delete(pin_record)

        # 3. Operación en Firestore: Resetear campos y activar el candado
        user_ref.update({
            "phone_number": "",           # Limpiamos el número equivocado
            "phone_validate": False,      # Por si acaso estaba en proceso
            "CambNumRegistro": True,      # Marcamos que ya usó su única oportunidad
            "last_reset_at": datetime.now(timezone.utc)
        })

        # 4. Audit Log
        register_action_log(
            db=db,
            establishment_id=user_id,
            action="REGISTRATION_PHONE_RESET",
            method="POST",
            path=request.url.path,
            payload={"msg": "User reset their registration phone number"},
            request=request
        )

        db.commit()
        return {
            "status": "success", 
            "message": "Registration phone has been reset. You can now register a new number."
        }

    except HTTPException as he:
        raise he
    except Exception as e:
        db.rollback()
        print(f"🚨 RESET PHONE ERROR: {str(e)}")
        raise HTTPException(status_code=500, detail="INTERNAL_SERVER_ERROR_ON_RESET")
