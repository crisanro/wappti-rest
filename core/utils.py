from .config import settings
from cryptography.fernet import Fernet
from sqlalchemy.orm import Session
from sqlalchemy import func
from fastapi import Request, HTTPException
from datetime import datetime, timezone, timedelta

# Importamos tus modelos
from models import SystemAudit, Establishment, SystemBlockedIP 

# --- CONFIGURACIÓN DE CIFRADO ---
SYSTEM_KEY = settings.SYSTEM_KEY

if not SYSTEM_KEY:
    # Evita que el sistema arranque sin la llave de seguridad
    raise RuntimeError("CRITICAL: SYSTEM_KEY no configurada en el entorno.")

try:
    cipher_suite = Fernet(SYSTEM_KEY.encode())
except Exception as e:
    raise RuntimeError(f"CRITICAL: SYSTEM_KEY inválida para Fernet. Error: {e}")

# --- FUNCIONES DE SEGURIDAD ---

def encrypt_value(plain_text: str) -> str:
    """Cifra un texto plano (Token) para guardarlo en la DB."""
    if not plain_text: return None
    return cipher_suite.encrypt(plain_text.encode()).decode()

def decrypt_value(encrypted_text: str) -> str:
    """Descifra un valor de la DB para usarlo en el servidor."""
    if not encrypted_text: return None
    try:
        return cipher_suite.decrypt(encrypted_text.encode()).decode()
    except Exception:
        raise HTTPException(status_code=500, detail="Error al descifrar credenciales.")

# --- FUNCIÓN DE AUDITORÍA ---

def register_action_log(
    db: Session, 
    establishment_id: str, 
    action: str, 
    method: str = "INTERNAL", 
    path: str = "/", 
    payload: dict = None, 
    request: Request = None,
    status_code: int = 200
):
    """
    Registra auditoría, actualiza last_use y detecta abusos.
    """
    # 1. Identificación de IP
    client_ip = "0.0.0.0"
    if request:
        forwarded = request.headers.get("X-Forwarded-For")
        client_ip = forwarded.split(",")[0] if forwarded else (request.client.host if request.client else "0.0.0.0")

    try:
        # 2. Guardar Log de Auditoría
        # Si el payload contiene tokens, asegúrate de no guardarlos en plano aquí
        new_log = SystemAudit(
            establishment_id=establishment_id,
            action=action,
            method=method,
            path=path,
            payload=payload if payload else {},
            ip=client_ip,
            status_code=status_code
        )
        db.add(new_log)

        # 3. Heartbeat del Establecimiento
        db.query(Establishment).filter(Establishment.id == establishment_id).update({
            "last_use": datetime.now(timezone.utc)
        })

        # 4. Detección de Abuso (Anti-DDoS)
        one_minute_ago = datetime.now(timezone.utc) - timedelta(minutes=1)
        request_count = db.query(SystemAudit).filter(
            SystemAudit.ip == client_ip,
            SystemAudit.created_at >= one_minute_ago
        ).count()

        if request_count > 40: 
            already_blocked = db.query(SystemBlockedIP).filter(SystemBlockedIP.ip_address == client_ip).first()
            if not already_blocked:
                db.add(SystemBlockedIP(
                    ip_address=client_ip, 
                    reason=f"Auto-block: {request_count} req/min"
                ))
            print(f"⚠️ IP BLOQUEADA: {client_ip}")

        db.commit()

    except Exception as e:
        db.rollback()
        print(f"❌ Error en utils.register_action_log: {str(e)}")