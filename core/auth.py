from .config import settings
import firebase_admin
from firebase_admin import credentials, auth
from fastapi import HTTPException, Depends, status, Security, Request, Header
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials, APIKeyHeader
from dotenv import load_dotenv

load_dotenv()

# --- FIREBASE CONFIGURATION ---
firebase_config = {
    "type": "service_account",
    "project_id": settings.FIREBASE_PROJECT_ID,
    "private_key_id": settings.FIREBASE_PRIVATE_KEY_ID,
    "private_key": settings.FIREBASE_PRIVATE_KEY.replace('\\n', '\n') if settings.FIREBASE_PRIVATE_KEY else None,
    "client_email": settings.FIREBASE_CLIENT_EMAIL,
    "token_uri": "https://oauth2.googleapis.com/token",
}

if not firebase_admin._apps:
    try:
        cred = credentials.Certificate(firebase_config)
        firebase_admin.initialize_app(cred)
    except Exception as e:
        print(f"❌ Error initializing Firebase: {e}")

# --- SECURITY SCHEMES DEFINITION ---

# 1. JWT para usuarios finales (App Móvil / FlutterFlow)
security_bearer = HTTPBearer()

# 2. API Key para Admin (n8n / Servicios generales)
admin_key_header = APIKeyHeader(name="X-Admin-Key", auto_error=False)

# 3. API Key para Superadmin (Rutas críticas / Solo tú)
superadmin_key_header = APIKeyHeader(name="X-Superadmin-Key", auto_error=False)


# --- VERIFICATION FUNCTIONS (DEPENDENCIES) ---

# A. Para Clientes (Usa JWT de Firebase)
def verify_firebase_token(auth_cred: HTTPAuthorizationCredentials = Depends(security_bearer)):
    """Validates the Firebase JWT token and returns the decoded payload."""
    token = auth_cred.credentials
    try:
        decoded_token = auth.verify_id_token(token, check_revoked=True)
        return decoded_token
    except auth.RevokedIdTokenError:
        raise HTTPException(status_code=401, detail="Token has been revoked")
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired Firebase token",
            headers={"WWW-Authenticate": "Bearer"},
        )

# B. Para Admin / n8n (Usa ADMIN_API_KEY)
def verify_admin_key(api_key: str = Security(admin_key_header)):
    """Validates the standard Admin API key."""
    master_key = settings.ADMIN_API_KEY
    if not master_key or api_key != master_key:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access Denied: Invalid Administrative API Key"
        )
    return api_key

# C. Para Superadmin / Rutas de Batch (Usa SUPERADMIN_API_KEY)
async def verify_superadmin_key(
    request: Request, 
    api_key: str = Security(superadmin_key_header)
):
    # 1. Validar la API Key
    secret = settings.SUPERADMIN_API_KEY
    if not secret or api_key != secret:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="NOT_AUTHORIZED_SUPERADMIN_ONLY"
        )

    # 2. Obtener la IP Real (Prioridad absoluta a Cloudflare)
    client_ip = request.headers.get("cf-connecting-ip") or \
                request.headers.get("x-forwarded-for", "").split(",")[0].strip() or \
                request.client.host

    # 3. Cargar lista de IPs (¡Pydantic ya la convirtió en lista por nosotros!)
    allowed_ips = settings.ALLOWED_SUPERADMIN_IPS

    # 4. VALIDACIÓN ESTRICTA
    if client_ip not in allowed_ips:
        print(f"❌ BLOQUEADO: La IP {client_ip} no coincide con ninguna permitida.")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"IP_NOT_AUTHORIZED: {client_ip}" 
        )

    print(f"✅ ACCESO CONCEDIDO a SuperAdmin via IP: {client_ip}")
    return api_key


# D. Para Administradores Humanos (Usa JWT + Whitelist de UIDs)
def verify_app_admin(token_data: dict = Depends(verify_firebase_token)):
    """
    Verifica que el usuario logueado en la App sea un Administrador autorizado.
    """
    # 1. Obtener la lista de UIDs permitidos (¡Pydantic ya la convirtió en lista!)
    allowed_uids = settings.ALLOWED_ADMIN_UIDS

    # 2. Extraer el UID del token decodificado
    user_uid = token_data.get("uid")

    # 3. Validar si el usuario está en la "Lista Blanca"
    if user_uid not in allowed_uids:
        print(f"❌ ACCESO DENEGADO: El UID {user_uid} no es Administrador.")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="NOT_AUTHORIZED_ADMIN_ONLY"
        )

    print(f"✅ ACCESO ADMIN CONCEDIDO: {user_uid}")
    return token_data


def verify_internal_key(x_wappti_key: str = Header(None)):
    """
    Valida que la petición incluya la llave secreta inyectada por el Proxy.
    """
    master_key = settings.INTERNAL_WAPPTI_KEY

    if not master_key or x_wappti_key != master_key:
        print(f"❌ Intento de acceso no autorizado. Header recibido: {x_wappti_key}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acceso denegado: Petición no autorizada por el Proxy oficial."
        )
    
    return True