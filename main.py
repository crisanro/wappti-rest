from fastapi import FastAPI, Request, HTTPException, Header, status
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from models import SystemBlockedIP
from core.database import SessionLocal

from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
# Asegúrate de importar SessionLocal y BlockedIP de tus archivos
# from database import SessionLocal 
# from models import BlockedIP

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

# --- 1. CACHE DE SEGURIDAD ---
blocked_ips_cache = set()

def update_blocked_ips_cache():
    """Consulta la DB y actualiza el set en memoria"""
    db = SessionLocal()
    try:
        # Obtenemos solo las IPs activas
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
        
        print(f"⏱️  {request.method} {request.url.path} | {process_time:.2f}ms | Status: {response.status_code}")
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
    version="0.0.1"
)


# =================================================================
# 🚀 PEGA EL EXCEPTION HANDLER AQUÍ (Justo después de 'app')
# =================================================================
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """
    Este bloque atrapará el error 422 y te mostrará en la consola
    exactamente qué campo falló y qué datos envió el cliente.
    """
    print("\n" + "="*50)
    print("❌ VALIDATION ERROR DETECTED")
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
# =================================================================

# --- 4. EVENTOS DE SISTEMA ---
@app.on_event("startup")
async def startup_event():
    # Carga las IPs al arrancar el servidor
    update_blocked_ips_cache()

# --- 5. ENDPOINTS DE SISTEMA ---

@app.post("/system/refresh-blacklist", tags=["System"])
async def refresh_blacklist(x_system_key: str = Header(None)):
    """
    Refresca el cache de IPs. 
    Protegido por una simple Header Key para evitar abusos.
    """
    # Define una clave secreta en tus variables de entorno idealmente
    SYSTEM_KEY = "tu_clave_secreta_aqui" 
    
    if x_system_key != SYSTEM_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized system action")
    
    update_blocked_ips_cache()
    return {
        "status": "success", 
        "current_cache_size": len(blocked_ips_cache)
    }

# 4. CONFIGURACIÓN DE MIDDLEWARES (Orden estratégico)
# El de tiempo envuelve a todos para medir el ciclo completo
app.add_middleware(TimeProcessMiddleware) 
# Luego la seguridad
app.add_middleware(IPBlockerMiddleware)
# Finalmente CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 6. Router Registration
# Organized with professional English prefixes
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

# --- ADMIN SECTION ---
# No es necesario pasar Depends aquí de nuevo porque ya lo pusimos en la definición del Router
app.include_router(admin_appointments.router)


# 7. Enhanced Health Check
@app.get("/", tags=["System"])
def health_check():
    return {
        "status": "online", 
        "version": app.version, 
        "server_time": "UTC",
    }

# Note: The 'registrar_log_actividad' function has been moved to core/utils.py 
# as 'register_action_log' to keep this main file clean and modular.