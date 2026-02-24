import os
import firebase_admin
from firebase_admin import credentials, auth
from fastapi import HTTPException, Depends, status, Security, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials, APIKeyHeader
from dotenv import load_dotenv

load_dotenv()

# --- FIREBASE CONFIGURATION ---
firebase_config = {
    "type": "service_account",
    "project_id": os.getenv("FIREBASE_PROJECT_ID"),
    "private_key_id": os.getenv("FIREBASE_PRIVATE_KEY_ID"),
    "private_key": os.getenv("FIREBASE_PRIVATE_KEY").replace('\\n', '\n') if os.getenv("FIREBASE_PRIVATE_KEY") else None,
    "client_email": os.getenv("FIREBASE_CLIENT_EMAIL"),
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
        decoded_token = auth.verify_id_token(token)
        return decoded_token
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired Firebase token",
            headers={"WWW-Authenticate": "Bearer"},
        )

# B. Para Admin / n8n (Usa ADMIN_API_KEY)
def verify_admin_key(api_key: str = Security(admin_key_header)):
    """Validates the standard Admin API key."""
    master_key = os.getenv("ADMIN_API_KEY")
    if not master_key or api_key != master_key:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access Denied: Invalid Administrative API Key"
        )
    return api_key

# C. Para Superadmin / Rutas de Batch (Usa SUPERADMIN_API_KEY)
# Define tus IPs permitidas en el .env (separadas por comas) o aquí mismo
# Ejemplo en .env: ALLOWED_ADMIN_IPS=1.2.3.4,5.6.7.8
ALLOWED_IPS = os.getenv("ALLOWED_SUPERADMIN_IPS", "").split(",")

async def verify_superadmin_key(
    request: Request, # <--- FastAPI inyecta el objeto request automáticamente
    api_key: str = Security(superadmin_key_header)
):
    """Valida la Superadmin API key y la IP de origen."""
    
    # 1. Validar la API Key primero
    secret = os.getenv("SUPERADMIN_API_KEY")
    if not secret or api_key != secret:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="NOT_AUTHORIZED_SUPERADMIN_ONLY"
        )

    # 2. Validar la IP (Opcional pero recomendado)
    # client.host extrae la IP que hace la petición
    client_ip = request.client.host
    
    # Si tienes una lista definida, verificamos que la IP esté ahí
    if ALLOWED_IPS and ALLOWED_IPS != ['']:
        if client_ip not in ALLOWED_IPS:
            print(f"🚫 Intento de acceso bloqueado desde IP no autorizada: {client_ip}")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="IP_NOT_AUTHORIZED"
            )

    return api_key

