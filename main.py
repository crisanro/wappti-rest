from fastapi import FastAPI, Request, HTTPException, Header, status
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from models import SystemBlockedIP
from core.database import SessionLocal

from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
import traceback

import time as time_lib

from routers.calendar import appointments, notes
from routers.communication import notifications, whatsapp
from routers.customers import base as base_custom
from routers.customers import tags as tags_custom
from routers.customers import finances, operation
from routers.establishments import base as base_estab
from routers.establishments import activity, financials, profile, tags
from routers.integrations import kipu
from routers.marketing import marketing, referral
from routers.support import support, validation, firestore
from routers.admin import appointments as admin_appointments
from routers.admin import notifications as admin_notifications
from routers.admin import feedback as admin_feedback
from routers.admin import establishments as admin_establishments

# --- 1. CACHE DE SEGURIDAD ---
blocked_ips_cache = set()

def update_blocked_ips_cache():
    """Consulta la DB y actualiza el set en memoria"""
    db = SessionLocal()
    try:
        blocked = db.query(SystemBlockedIP.ip_address).filter(SystemBlockedIP.is_active == True).all()
        global blocked_ips_cache
        blocked_ips_cache = {ip[0] for ip in blocked}
        print(f"✔️ Blacklist actualizada: {len(blocked_ips_cache)} IPs bloqueadas.")
    except Exception as e:
        print(f"❌ Error actualizando blacklist: {e}")
    finally:
        db.close()

# --- 2. MIDDLEWARES ---

class TimeProcessMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start_time = time_lib.perf_counter()
        response = await call_next(request)
        process_time = (time_lib.perf_counter() - start_time) * 1000
        
        full_url = str(request.url)
        
        # 🔴 Resalta visualmente los errores en consola
        if response.status_code >= 500:
            print(f"💥 {request.method} | {full_url} | {process_time:.2f}ms | Status: {response.status_code}")
        elif response.status_code >= 400:
            print(f"⚠️  {request.method} | {full_url} | {process_time:.2f}ms | Status: {response.status_code}")
        else:
            print(f"⏱️  {request.method} | {full_url} | {process_time:.2f}ms | Status: {response.status_code}")
        
        response.headers["X-Process-Time"] = f"{process_time:.2f}ms"
        return response

class IPBlockerMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        forwarded = request.headers.get("X-Forwarded-For")
        client_ip = forwarded.split(",")[0] if forwarded else (request.client.host if request.client else "0.0.0.0")

        if client_ip in blocked_ips_cache:
            raise HTTPException(
                status_code=403, 
                detail="Access denied. Your IP has been flagged for suspicious activity."
            )
        return await call_next(request)

# --- 3. INSTANCIA DE APP ---
app = FastAPI(
    title="WAPPTI API",
    description="Central connection point for business management and automation",
    version="0.0.1",
    root_path="/api/v1"
)

# =================================================================
# 🛡️ EXCEPTION HANDLERS - Captura y muestra todos los errores
# =================================================================

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Captura errores 422 - Campos inválidos o faltantes"""
    print("\n" + "="*50)
    print("❌ VALIDATION ERROR (422)")
    print(f"🌐 URL: {request.method} {request.url}")
    print(f"📝 Errors: {exc.errors()}")
    print(f"📦 Body Sent: {exc.body}")
    print("="*50 + "\n")
    
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "detail": exc.errors(), 
            "body_received": exc.body
        },
    )

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """Captura errores HTTP conocidos (403, 404, 401, etc.)"""
    print("\n" + "="*50)
    print(f"🚨 HTTP ERROR ({exc.status_code})")
    print(f"🌐 URL: {request.method} {request.url}")
    print(f"📝 Detail: {exc.detail}")
    print("="*50 + "\n")

    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail}
    )

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Captura cualquier error inesperado (500)"""
    print("\n" + "="*50)
    print("💥 UNHANDLED EXCEPTION (500)")
    print(f"🌐 URL: {request.method} {request.url}")
    print(f"❌ Error Type: {type(exc).__name__}")
    print(f"📝 Message: {str(exc)}")
    print(f"🔍 Traceback:\n{traceback.format_exc()}")
    print("="*50 + "\n")
    
    return JSONResponse(
        status_code=500,
        content={
            "error": type(exc).__name__,
            "message": str(exc)
        }
    )

# =================================================================

# --- 4. EVENTOS DE SISTEMA ---
@app.on_event("startup")
async def startup_event():
    update_blocked_ips_cache()

# --- 5. ENDPOINTS DE SISTEMA ---

@app.post("/system/refresh-blacklist", tags=["System"])
async def refresh_blacklist(x_system_key: str = Header(None)):
    """Refresca el cache de IPs. Protegido por Header Key."""
    SYSTEM_KEY = "tu_clave_secreta_aqui" 
    
    if x_system_key != SYSTEM_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized system action")
    
    update_blocked_ips_cache()
    return {
        "status": "success", 
        "current_cache_size": len(blocked_ips_cache)
    }

# --- 6. CONFIGURACIÓN DE MIDDLEWARES (Orden estratégico) ---
app.add_middleware(TimeProcessMiddleware) 
app.add_middleware(IPBlockerMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- 7. REGISTRO DE ROUTERS ---
app.include_router(base_estab.router, prefix="/establishment", tags=["Establishments"])
app.include_router(activity.router, prefix="/establishment", tags=["Establishments"])
app.include_router(profile.router, prefix="/profile", tags=["Establishments"])
app.include_router(tags.router, prefix="/tags", tags=["Establishments"])
app.include_router(financials.router, prefix="/financials", tags=["Establishments"])

app.include_router(base_custom.router, prefix="/customer", tags=["Customers"])
app.include_router(finances.router, prefix="/customer", tags=["Customers"])
app.include_router(tags_custom.router, prefix="/customer", tags=["Customers"])

app.include_router(kipu.router, prefix="/kipu", tags=["Integraciones"])

app.include_router(operation.router, prefix="/operation", tags=["Operations"])
app.include_router(appointments.router, prefix="/appointments", tags=["Operations"])
app.include_router(notes.router, prefix="/notes", tags=["Operations"])

app.include_router(marketing.router, prefix="/marketing", tags=["Marketing"])
app.include_router(referral.router, prefix="/referral", tags=["Marketing"])

app.include_router(whatsapp.router, prefix="/whatsapp", tags=["WhatsApp & Notifications"])
app.include_router(notifications.router, prefix="/notifications", tags=["WhatsApp & Notifications"])

app.include_router(support.router, prefix="/support", tags=["Support & Feedback"])
app.include_router(validation.router, prefix="/validation", tags=["Validation"])
app.include_router(firestore.router, tags=["Validation"])

app.include_router(admin_appointments.router)
app.include_router(admin_notifications.router)
app.include_router(admin_feedback.router)
app.include_router(admin_establishments.router)

# --- 8. HEALTH CHECK ---
@app.get("/", tags=["System"])
def health_check():
    return {
        "status": "online", 
        "version": app.version, 
        "server_time": "UTC",
    }
