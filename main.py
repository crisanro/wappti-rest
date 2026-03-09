import sentry_sdk
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration
import os
import traceback
import time as time_lib
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, HTTPException, Header, status
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

# Importaciones internas
from models import SystemBlockedIP
from core.database import SessionLocal

# Importaciones de Routers
from routers.calendar import appointments, notes
from routers.communication import notifications, whatsapp
from routers.customers import base as base_custom
from routers.customers import tags as tags_custom
from routers.customers import finances, operation
from routers.establishments import base as base_estab
from routers.establishments import activity, financials, profile, tags
from routers.integrations import kipu
from routers.marketing import marketing, referral
from routers.integrations import wapptiweb
from routers.support import support, validation, firestore
from routers.admin import appointments as admin_appointments
from routers.admin import notifications as admin_notifications
from routers.admin import feedback as admin_feedback
from routers.admin import establishments as admin_establishments
from routers.admin import control as admin_control
from routers.admin_app import admin as admin_dash
from routers.admin_app import finance as finance_dash

# --- 0. CONFIGURACIÓN DE SENTRY ---
sentry_sdk.init(
    dsn=os.getenv("SENTRY_DSN"),
    integrations=[
        FastApiIntegration(),
        SqlalchemyIntegration(),
    ],
    traces_sample_rate=0.1,  # Optimizado para 0.5 CPU
    profiles_sample_rate=0.1,
    environment="production",
)

# Configuración de Debug desde variables de entorno
DEBUG_MODE = os.getenv("DEBUG", "False").lower() == "true"

# --- 1. CACHE DE SEGURIDAD (BLACKLIST) ---
blocked_ips_cache = set()
last_blacklist_update = 0
BLACKLIST_REFRESH_INTERVAL = 300  # 5 minutos

def update_blocked_ips_cache():
    """Consulta la DB y actualiza el set en memoria del worker actual"""
    db = SessionLocal()
    try:
        blocked = db.query(SystemBlockedIP.ip_address).filter(SystemBlockedIP.is_active == True).all()
        global blocked_ips_cache
        blocked_ips_cache = {ip[0] for ip in blocked}
    except Exception as e:
        print(f"❌ Error actualizando blacklist: {e}")
    finally:
        db.close()

# --- 2. MANEJO DE LIFESPAN (Sustituye a startup_event) ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    # STARTUP: Se ejecuta al encender el servidor/worker
    print("🚀 Servidor WAPPTI iniciando...")
    update_blocked_ips_cache()
    yield
    # SHUTDOWN: Se ejecuta al apagar el servidor
    print("🛑 Servidor WAPPTI apagándose...")

# --- 3. MIDDLEWARES ---

class TimeProcessMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start_time = time_lib.perf_counter()
        response = await call_next(request)
        process_time = (time_lib.perf_counter() - start_time) * 1000
        
        full_url = str(request.url)
        # Resalta errores en consola de forma visual
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
        global last_blacklist_update, blocked_ips_cache
        
        # Obtener IP (Considerando proxies como Nginx/Cloudflare)
        forwarded = request.headers.get("X-Forwarded-For")
        client_ip = forwarded.split(",")[0] if forwarded else (request.client.host if request.client else "0.0.0.0")

        # Refresco automático por tiempo para sincronizar workers
        current_time = time_lib.time()
        if (current_time - last_blacklist_update) > BLACKLIST_REFRESH_INTERVAL:
            update_blocked_ips_cache()
            last_blacklist_update = current_time

        if client_ip in blocked_ips_cache:
            raise HTTPException(
                status_code=403, 
                detail="Access denied. Your IP has been flagged for suspicious activity."
            )
        return await call_next(request)

# --- 4. INSTANCIA DE APP ---
app = FastAPI(
    title="WAPPTI API",
    description="Central connection point for business management and automation",
    version="0.0.1",
    debug=DEBUG_MODE,
    root_path="/api/v1",
    lifespan=lifespan
)

# --- 5. EXCEPTION HANDLERS ---

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    print(f"\n❌ VALIDATION ERROR (422)\nURL: {request.url}\nErrors: {exc.errors()}\n")
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"detail": exc.errors(), "body_received": exc.body},
    )

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    print(f"\n🚨 HTTP ERROR ({exc.status_code})\nURL: {request.url}\nDetail: {exc.detail}\n")
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail}
    )

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    # Enviamos el error real a Sentry
    sentry_sdk.capture_exception(exc)

    # Log detallado para consola interna
    print(f"\n💥 UNHANDLED EXCEPTION (500)\nURL: {request.url}\n{traceback.format_exc()}\n")
    
    # Respuesta genérica al cliente por seguridad
    return JSONResponse(
        status_code=500,
        content={
            "error": "InternalServerError",
            "message": "An unexpected error occurred on our end. Our team has been notified."
        }
    )

# --- 6. ENDPOINTS DE SISTEMA ---

@app.post("/system/refresh-blacklist", tags=["System"])
async def refresh_blacklist(x_system_key: str = Header(None)):
    """Refresca el cache de IPs. Protegido por SYSTEM_KEY en Env."""
    SYSTEM_KEY = os.getenv("SYSTEM_KEY") 
    
    if not SYSTEM_KEY or x_system_key != SYSTEM_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized system action")
    
    update_blocked_ips_cache()
    return {
        "status": "success", 
        "current_cache_size": len(blocked_ips_cache)
    }

# --- 7. CONFIGURACIÓN DE MIDDLEWARES ---
app.add_middleware(TimeProcessMiddleware) 
app.add_middleware(IPBlockerMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # Cambiar por dominios específicos en producción
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- 8. REGISTRO DE ROUTERS ---
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
app.include_router(wapptiweb.router, prefix="/marketing", tags=["Marketing"])
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
app.include_router(admin_control.router)
app.include_router(finance_dash.router)
app.include_router(admin_dash.router)

# --- 9. HEALTH CHECK ---
@app.get("/", tags=["System"])
def health_check():
    return {
        "status": "online", 
        "version": app.version, 
        "server_time": str(time_lib.strftime("%Y-%m-%d %H:%M:%S")),
    }