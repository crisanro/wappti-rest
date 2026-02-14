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

# Initialize Firebase only once
if not firebase_admin._apps:
    try:
        cred = credentials.Certificate(firebase_config)
        firebase_admin.initialize_app(cred)
    except Exception as e:
        print(f"❌ Error initializing Firebase: {e}")

# --- SECURITY SCHEMES ---
# 1. For Mobile App / Frontend (JWT)
security_bearer = HTTPBearer()

# 2. For n8n / Admin / External Automations (API KEY)
api_key_header = APIKeyHeader(name="X-Admin-Key", auto_error=False)

# --- VERIFICATION FUNCTIONS ---

# A. Verification for Clients (FlutterFlow / Web)
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

# B. Verification for Admin / System Services (n8n)
def verify_admin_key(api_key: str = Security(api_key_header)):
    """Validates the master API key for administrative or automated tasks."""
    master_key = os.getenv("ADMIN_API_KEY")
    
    if api_key and api_key == master_key:
        return True
    
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Access Denied: Invalid Administrative API Key"
    )