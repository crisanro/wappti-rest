from fastapi import APIRouter, BackgroundTasks, HTTPException, Depends
from pydantic import BaseModel, EmailStr
from firebase_admin import auth
from firebase_admin.auth import UserNotFoundError
from datetime import datetime, timezone
from core.auth import verify_firebase_token # Asegúrate de que la ruta sea correcta
from services.email_service import process_password_reset_email, process_email_verification

router = APIRouter(tags=["Authentication"])

class EmailRequest(BaseModel):
    email: EmailStr

email_request_logs = {}
COOLDOWN_SECONDS = 60

def check_and_update_cooldown(identifier: str):
    """
    Verifica si han pasado 60 segundos desde la última petición.
    Lanza error 429 si el usuario debe esperar.
    """
    now = datetime.now(timezone.utc)
    last_request_time = email_request_logs.get(identifier)

    if last_request_time:
        time_passed = (now - last_request_time).total_seconds()
        if time_passed < COOLDOWN_SECONDS:
            remaining = int(COOLDOWN_SECONDS - time_passed)
            # Lanzamos error 429: Demasiadas peticiones
            raise HTTPException(
                status_code=429, 
                detail=f"wait_{remaining}_seconds"
            )
    
    # Si pasa la validación, actualizamos la hora de su última petición
    email_request_logs[identifier] = now

@router.post("/recover-password")
async def recover_password(request: EmailRequest, background_tasks: BackgroundTasks):
    """Solicita un correo para restablecer la contraseña (Ruta Pública)"""
    
    # 1. Validamos el tiempo de espera ANTES de hacer nada más.
    # Si no han pasado 60s, esto lanzará un Error 429 (Too Many Requests) y detendrá la ejecución.
    check_and_update_cooldown(request.email)
    
    try:
        # 2. Validamos que el usuario realmente exista en Firebase
        user = auth.get_user_by_email(request.email)
        
        # 3. Si existe, encolamos el correo usando el email exacto que Firebase nos devuelve
        background_tasks.add_task(process_password_reset_email, user.email)
        
    except UserNotFoundError:
        # Silencio absoluto si el correo no existe para evitar ataques de enumeración
        pass
    except Exception as e:
        # Imprimimos el error interno para debug, pero no se lo mostramos al usuario
        print(f"❌ Error consultando Firebase en recover_password: {e}")

    # 4. Siempre retornamos ok: True, sea cual sea el resultado (siempre y cuando haya pasado el cooldown)
    return {"ok": True}


@router.post("/verify-email")
async def verify_email(
    background_tasks: BackgroundTasks,
    token_data: dict = Depends(verify_firebase_token)
):
    """Envía el correo de verificación de cuenta (Ruta Privada)"""
    
    uid = token_data.get("uid")
    
    # 1. Validamos el tiempo de espera usando su UID
    check_and_update_cooldown(uid)
    
    try:
        user = auth.get_user(uid)
        
        if user.email_verified:
            raise HTTPException(
                status_code=400, 
                detail="email_already_verified"
            )
            
        background_tasks.add_task(process_email_verification, user.email)
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Error en verify_email: {e}")
        raise HTTPException(status_code=500, detail="verification_process_error")
    
    return {"ok": True}