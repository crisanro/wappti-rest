from core.config import settings
import httpx
import random
import pytz
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status, Query
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified
from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter
from google.oauth2 import service_account
from dotenv import load_dotenv

# Core & Auth
from core.database import get_db
from core.auth import verify_firebase_token
from core.utils import register_action_log

# Models & Schemas
from models import (
    WhatsAppAuthPin, ReferralCode, Establishment, 
    UsageAuditLog, AppNotification, SystemAudit, ReferralMKTCampaigns
)
from schemas.validation import (
    CheckPhoneSchema, PinRequestSchema, 
    VerifyPinSchema, LinkReferralRequest
)

import traceback

# --- CONFIGURACIÓN ---
# Esta es la URL exclusiva para tus notificaciones y logs de seguimiento
WEBHOOK_URL_NOTIFICATIONS = settings.WEBHOOK_URL_NOTIFICATIONS

load_dotenv()

async def fire_security_webhook(event_type: str, user_id: str, details: dict, request: Request):
    webhook_url = settings.SECURITY_WEBHOOK_URL
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
    project_id = settings.FIREBASE_PROJECT_ID
    private_key = settings.FIREBASE_PRIVATE_KEY
    client_email = settings.FIREBASE_CLIENT_EMAIL
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

async def notify_log(event_name: str, user_id: str, data: dict):
    """
    Función auxiliar para enviar logs y alertas a tu webhook de notificaciones.
    """
    if not WEBHOOK_URL_NOTIFICATIONS:
        return

    payload = {
        "event": event_name,
        "user_id": user_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "metadata": data
    }
    
    try:
        async with httpx.AsyncClient() as client:
            # Enviamos el log sin esperar que el webhook procese todo (timeout corto)
            await client.post(WEBHOOK_URL_NOTIFICATIONS, json=payload, timeout=5.0)
    except Exception as e:
        print(f"⚠️ No se pudo enviar el log al webhook: {e}")


# Inicializamos el cliente
db_firestore = get_firestore_client()
# Inicializamos el cliente de Firestore

router = APIRouter(dependencies=[Depends(verify_firebase_token)])

@router.get("/check-phone")
async def check_firestore_phone(
    request: Request,
    phone: str = Query(..., description="Phone to validate"),
    db: Session = Depends(get_db),
    token_data: dict = Depends(verify_firebase_token)
):
    establishment_id = str(token_data.get('uid'))
    
    try:
        # 1. Consultar Firestore (Sintaxis corregida)
        users_ref = db_firestore.collection("users")
        
        # Usamos .where(filter=FieldFilter(...))
        # Esto cumple con la nueva normativa y elimina el UserWarning
        query = users_ref.where(filter=FieldFilter("phone_number", "==", phone)).limit(1).get()
        
        is_unique = len(query) == 0
        status_msg = "SUCCESS_UNIQUE" if is_unique else "REJECTED_DUPLICATE"

        # 2. Notificación proactiva
        if not is_unique:
            await notify_log("ALERT_PHONE_DUPLICATE_ATTEMPT", establishment_id, {
                "phone_queried": phone,
                "ip": request.client.host
            })

        # 3. Auditoría en SQL
        new_log = SystemAudit(
            establishment_id=establishment_id,
            action="PHONE_UNIQUENESS_CHECK",
            method="GET",
            path=request.url.path,
            payload={"phone_queried": phone, "result": status_msg},
            status_code=200 if is_unique else 400,
            created_at=datetime.now(timezone.utc)
        )
        db.add(new_log)
        db.commit()

        # 4. Respuesta Final
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
        # El webhook de n8n te avisará si esto vuelve a fallar
        await notify_log("ERROR_CHECK_PHONE_SYSTEM", establishment_id, {
            "error": str(e),
            "trace": traceback.format_exc()[-500:]
        })
        print(f"🚨 CHECK_PHONE_ERROR: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail="INTERNAL_SERVER_ERROR")


@router.post("/validate-and-activate", status_code=status.HTTP_200_OK)
async def validate_and_activate(
    data: VerifyPinSchema, 
    request: Request,
    db: Session = Depends(get_db), 
    token_data: dict = Depends(verify_firebase_token)
):
    user_id = str(token_data.get('uid'))
    
    try:
        # 1. Obtener registro del PIN
        record = db.query(WhatsAppAuthPin).filter(WhatsAppAuthPin.id == user_id).first()
        if not record:
            raise HTTPException(status_code=404, detail="RECORD_NOT_FOUND")
        
        if record.is_activated:
            raise HTTPException(status_code=400, detail="ALREADY_ACTIVATED")

        # 2. Seguridad: Verificación de teléfono
        if record.associated_phone != data.phone:
            await notify_log("ALERT_PHONE_MISMATCH_ACTIVATION", user_id, {
                "expected": record.associated_phone,
                "got": data.phone
            })
            raise HTTPException(status_code=403, detail="SECURITY_PHONE_MISMATCH")

        # 3. Lógica de PIN e Intentos (Se mantiene igual)
        attempts = list(record.validation_attempts or [])
        if len(attempts) >= 3:
            raise HTTPException(status_code=429, detail="TOO_MANY_ATTEMPTS")

        if record.pin != data.pin:
            attempts.append(data.pin)
            record.validation_attempts = attempts
            flag_modified(record, "validation_attempts")
            db.commit() 
            raise HTTPException(status_code=400, detail={"msg": "INVALID_PIN", "attempts": len(attempts)})

        # --- NUEVA LÓGICA DE RECOMPENSAS POR REFERIDOS/CAMPAÑAS ---
        
        reward = 10  # Recompensa base
        final_ref_id = None
        is_marketing = False

        if data.referred_by:
            # A. Primero buscamos si es una Campaña de Marketing
            # El prefijo "CAMP_" nos ayuda a identificarlo si lo guardaste así en /link-referral
            campaign_id_clean = data.referred_by.replace("CAMP_", "")
            
            campaign = None
            if data.referred_by.startswith("CAMP_") or data.referred_by.isdigit():
                campaign = db.query(ReferralMKTCampaigns).filter(
                    ReferralMKTCampaigns.id == int(campaign_id_clean)
                ).first()

            if campaign:
                reward = 10 + campaign.bonus_credits  # 10 base + bono (ej. 10) = 20
                final_ref_id = f"CAMP_{campaign.id}"
                is_marketing = True
                
                # Actualizamos lista de la campaña
                camp_list = list(campaign.used_by_list or [])
                if user_id not in camp_list:
                    camp_list.append(user_id)
                    campaign.used_by_list = camp_list
                    flag_modified(campaign, "used_by_list")
            
            else:
                # B. Si no es campaña, buscamos si es Referido Humano
                ref_record = db.query(ReferralCode).filter(ReferralCode.id == data.referred_by).first()
                if ref_record:
                    reward = 20  # Recompensa estándar por referido humano
                    final_ref_id = ref_record.id
                    
                    # Actualizar contador del dueño del código
                    ref_record.user_count = (ref_record.user_count or 0) + 1
                    ref_list = list(ref_record.users_list or [])
                    if user_id not in ref_list:
                        ref_list.append(user_id)
                        ref_record.users_list = ref_list
                        flag_modified(ref_record, "users_list")

        # --- BLOQUE DE ACTIVACIÓN FINAL ---

        # 1. Sincronización Firestore (Créditos iniciales)
        if not update_user_reminders(user_id, reward):
            raise Exception("FIRESTORE_SYNC_FAILED")

        # 2. SQL: Actualizar Establecimiento
        db.query(Establishment).filter(Establishment.id == user_id).update({
            "country": data.country,
            "whatsapp": str(data.phone),
            "created_at": datetime.now(timezone.utc),
            "referred_by": final_ref_id,
            "available_credits": Establishment.available_credits + reward,
            "is_suspended": False
        }, synchronize_session=False)

        # 3. SQL: Audit Log
        observations = f"Welcome bonus"
        if is_marketing:
            observations += f" (MKT Campaign: {final_ref_id})"
        elif final_ref_id:
            observations += f" (Ref: {final_ref_id})"

        db.add(UsageAuditLog(
            establishment_id=user_id, 
            condition="top-up", 
            value=reward, 
            observations=observations
        ))
        
        # 4. Finalizar proceso
        record.is_activated = True
        db.commit()

        return {"complete": True, "reward_applied": reward, "type": "marketing" if is_marketing else "standard"}

    except HTTPException as he:
        raise he
    except Exception as e:
        db.rollback()
        await notify_log("ERROR_ACTIVATION_SYSTEM", user_id, {"error": str(e)})
        raise HTTPException(status_code=500, detail="INTERNAL_SERVER_ERROR")
    

# --- 4. LINK REFERRAL CODE ---
@router.post("/link-referral")
async def link_referral_code(
    data: LinkReferralRequest, 
    request: Request,
    db: Session = Depends(get_db),
    token_data: dict = Depends(verify_firebase_token)
):
    try:
        establishment_id = str(token_data.get('uid'))
        clean_code = "".join(data.code_text.split()).lower()

        # 1. Validación de existencia del negocio
        establishment = db.query(Establishment).filter(Establishment.id == establishment_id).first()
        if not establishment:
            raise HTTPException(status_code=404, detail="ESTABLISHMENT_NOT_FOUND")
        
        if establishment.referred_by:
            raise HTTPException(status_code=400, detail="ALREADY_REFERRED")

        # 2. BÚSQUEDA JERÁRQUICA
        # A. Intentar buscar en Campañas de Marketing
        campaign = db.query(ReferralMKTCampaigns).filter(
            ReferralMKTCampaigns.code == clean_code
        ).first()

        if campaign:
            # Validamos si tiene fecha de expiración y si ya pasó
            if campaign.expires_at and datetime.now(pytz.UTC) > campaign.expires_at:
                await notify_log("ALERT_EXPIRED_CAMPAIGN_ATTEMPT", establishment_id, {"code": clean_code})
                # Lanzamos un error específico para que el usuario sepa que llegó tarde
                raise HTTPException(
                    status_code=400, 
                    detail="CAMPAIGN_EXPIRED"
                )
            # Si pasó la validación de fecha, 'campaign' sigue teniendo el objeto y el código continúa...
        # B. Si no es campaña, buscar en Referidos Humanos
        referral_record = None
        if not campaign:
            referral_record = db.query(ReferralCode).filter(ReferralCode.code == clean_code).first()

        if not campaign and not referral_record:
            await notify_log("ALERT_INVALID_CODE", establishment_id, {"attempted": clean_code})
            raise HTTPException(status_code=404, detail="INVALID_CODE")

        # 3. LÓGICA DE VINCULACIÓN (Security Checks)
        
        # Evitar que el usuario use su propio código humano
        if referral_record and referral_record.id == establishment_id:
            raise HTTPException(status_code=400, detail="CANNOT_REFER_YOURSELF")

        # Verificar si ya usó esta campaña específica anteriormente
        if campaign:
            current_campaign_users = list(campaign.used_by_list or [])
            if establishment_id in current_campaign_users:
                raise HTTPException(status_code=400, detail="CAMPAIGN_ALREADY_USED")
            
            # Asignamos el ID de la campaña (convertido a string para la FK de referred_by)
            # Nota: Al ser INT, podrías usar un prefijo "CAMP_" o simplemente guardarlo.
            establishment.referred_by = f"CAMP_{campaign.id}"
            
            # Actualizamos la lista de la campaña
            current_campaign_users.append(establishment_id)
            campaign.used_by_list = current_campaign_users
            flag_modified(campaign, "used_by_list")
            
        else:
            # Lógica para referido humano
            current_ref_users = list(referral_record.users_list or [])
            if establishment_id in current_ref_users:
                 raise HTTPException(status_code=400, detail="REFERRAL_ALREADY_CLAIMED")
            
            establishment.referred_by = referral_record.id
            referral_record.user_count = (referral_record.user_count or 0) + 1
            current_ref_users.append(establishment_id)
            referral_record.users_list = current_ref_users
            flag_modified(referral_record, "users_list")

        # 4. Sincronización Firestore (Opcional, manteniendo tu lógica)
        try:
            if db_firestore:
                user_ref = db_firestore.collection("users").document(establishment_id)
                user_ref.update({
                    "Referido": establishment.referred_by,
                    "referral_code_used": clean_code,
                    "is_marketing_campaign": campaign is not None,
                    "updated_at": datetime.now(timezone.utc)
                })
        except Exception as fs_error:
            await notify_log("ERROR_FS_SYNC", establishment_id, {"error": str(fs_error)})

        db.commit()

        # 5. Respuesta
        return {
            "status": "success", 
            "type": "marketing" if campaign else "human",
            "message": "Code linked. Benefits will activate upon WhatsApp verification."
        }
        
    except HTTPException as he:
        raise he
    except Exception as e:
        db.rollback()
        # Aquí puedes usar sentry_sdk.capture_exception(e) si lo tienes configurado
        raise HTTPException(status_code=500, detail="INTERNAL_SERVER_ERROR")


@router.post("/reset-registration-phone")
async def reset_registration_phone(
    request: Request,
    db: Session = Depends(get_db),
    token_data: dict = Depends(verify_firebase_token)
):
    user_id = str(token_data.get('uid'))

    try:
        user_ref = db_firestore.collection("users").document(user_id)
        user_doc = user_ref.get()

        if not user_doc.exists:
            raise HTTPException(status_code=404, detail="USER_NOT_FOUND")

        user_data = user_doc.to_dict()
        
        # 1. Alerta: Intento en cuenta activa
        if user_data.get("is_activated") is True:
            await notify_log("ALERT_RESET_ACTIVE_ACCOUNT", user_id, {"ip": request.client.host})
            raise HTTPException(status_code=403, detail="ACCOUNT_ALREADY_ACTIVE")

        # 2. Alerta: Intento de bypass de candado
        if user_data.get("CambNumRegistro") is True:
            await notify_log("ALERT_MULTIPLE_RESET_ATTEMPT", user_id, {"ip": request.client.host})
            raise HTTPException(status_code=403, detail="PHONE_CHANGE_ALREADY_USED")

        # 3. Proceso de Reset
        pin_record = db.query(WhatsAppAuthPin).filter(WhatsAppAuthPin.id == user_id).first()
        old_phone = pin_record.associated_phone if pin_record else "none"
        
        if pin_record:
            db.delete(pin_record)

        user_ref.update({
            "phone_number": "",
            "phone_validate": False,
            "CambNumRegistro": True,
            "last_reset_at": datetime.now(timezone.utc)
        })

        # 4. Éxito: Notificar log de operación exitosa
        await notify_log("LOG_PHONE_RESET_SUCCESS", user_id, {
            "old_phone": old_phone,
            "action": "registration_restart"
        })

        db.commit()
        return {"status": "success", "message": "Phone reset successful."}

    except HTTPException as he:
        raise he
    except Exception as e:
        db.rollback()
        # 5. Alerta: Error de sistema (Muy importante para tu cel)
        await notify_log("ERROR_SYSTEM_CRITICAL", user_id, {
            "error": str(e),
            "trace": traceback.format_exc()[-500:] # Enviamos los últimos 500 caracteres del error
        })
        raise HTTPException(status_code=500, detail="INTERNAL_SERVER_ERROR")
