import os
import firebase_admin
from firebase_admin import credentials, auth
from fastapi import HTTPException, Depends, status, Security
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
async def verify_superadmin_key(api_key: str = Security(superadmin_key_header)):
    """Validates the Superadmin API key for high-privilege operations."""
    secret = os.getenv("SUPERADMIN_API_KEY")
    if not secret or api_key != secret:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="NOT_AUTHORIZED_SUPERADMIN_ONLY"
        )
    return api_key