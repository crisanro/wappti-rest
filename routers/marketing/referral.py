import re
from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy.orm import Session
from sqlalchemy import func, text
from datetime import datetime, timedelta, timezone
import traceback
import pytz
from core.database import get_db
from core.auth import verify_firebase_token
from core.utils import register_action_log
from models import *
# Import English models
from schemas.financials import PaymentCreate, PayoutMethodCreate, WithdrawalRequestCreate, StripeCheckoutSchema, ActivateReferralRequest
from services.stripe_service import StripeService

router = APIRouter(dependencies=[Depends(verify_firebase_token)])

# --- REFERRALS & BALANCES SECTION ---
@router.get("/dashboard")
def get_referral_dashboard(
    timezone_name: str = "America/Guayaquil", 
    db: Session = Depends(get_db), 
    token_data: dict = Depends(verify_firebase_token)
):
    try:
        uid = token_data.get('uid')
        
        # 1. Zona horaria
        try:
            local_tz = pytz.timezone(timezone_name)
        except Exception:
            local_tz = pytz.UTC

        def format_dt(dt):
            if not dt: return None
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=pytz.UTC)
            return dt.astimezone(local_tz).isoformat()

        # 2. INFO GENERAL
        referral_info = db.query(ReferralCode).filter(ReferralCode.id == uid).first()
        
        # 3. BALANCE HISTORIAL
        balance_db = db.query(ReferralBalance).filter(
            ReferralBalance.referred_customer_id == uid
        ).order_by(ReferralBalance.created_at.desc()).all()

        # 4. MÉTODOS DE PAGO (Solo para el resumen/configuración)
        payout_methods = db.query(ReferralPayoutMethod).filter(
            ReferralPayoutMethod.establishment_id == uid
        ).all()

        # 5. SOLICITUDES DE RETIRO (Usando tus columnas directas)
        withdrawals_db = db.query(ReferralWithdrawal).filter(
            ReferralWithdrawal.establishment_id == uid
        ).order_by(ReferralWithdrawal.created_at.desc()).all()

        return {
            "summary": {
                "my_code": referral_info.code if referral_info else None,
                "total_referred_count": referral_info.user_count if referral_info else 0,
                "current_balance": balance_db[0].balance if balance_db else 0.0,
                "needs_payout_setup": len(payout_methods) == 0
            },
            "balance_history": [
                {
                    "id": b.id,
                    "amount": b.amount,
                    "balance_after": b.balance,
                    "created_at": format_dt(b.created_at)
                } for b in balance_db
            ],
            "payout_methods": [
                {
                    "id": p.id,
                    "platform": p.platform,
                    "account_details": p.account_details
                } for p in payout_methods
            ],
            "withdrawals": [
                {
                    "id": w.id,
                    "amount": w.amount,
                    "status": w.status,
                    "platform": w.platform, # <--- Directo de la tabla withdrawals
                    "account": w.account,   # <--- Directo de la tabla withdrawals
                    "created_at": format_dt(w.created_at)
                } for w in withdrawals_db
            ]
        }

    except Exception as e:
        import traceback
        print(f"🚨 ERROR DASHBOARD: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail="error_fetching_dashboard")


@router.get("/balance", response_model=list[dict])
def get_referral_balance(
    timezone_name: str = "America/Guayaquil", 
    db: Session = Depends(get_db), 
    token_data: dict = Depends(verify_firebase_token)
):
    try:
        establishment_id = token_data.get('uid')
        
        # 1. Consulta filtrada por el local
        referrals_db = db.query(ReferralBalance).filter(
            ReferralBalance.referred_customer_id == establishment_id
        ).order_by(ReferralBalance.created_at.desc()).all()

        # 2. Configuración de zona horaria
        try:
            local_tz = pytz.timezone(timezone_name)
        except Exception:
            local_tz = pytz.UTC

        # 3. Respuesta limpia: sin reference_data ni referred_customer_id
        return [
            {
                "id": r.id,
                "amount": r.amount,
                "balance": r.balance,
                "created_at": r.created_at.astimezone(local_tz).isoformat() if r.created_at else None
            } for r in referrals_db
        ]

    except Exception as e:
        print(f"🚨 Error en referrals: {str(e)}")
        raise HTTPException(status_code=500, detail="error_fetching_referrals")

# --- ACCOUNTS & WITHDRAWALS ---

@router.post("/payout-methods", status_code=status.HTTP_201_CREATED)
def add_payout_method(
    data: PayoutMethodCreate, 
    db: Session = Depends(get_db),
    token_data: dict = Depends(verify_firebase_token)
):
    """
    Registra un nuevo método de pago validando que no sea un duplicado.
    """
    my_id = token_data.get('uid')

    # 1. Normalizamos los datos para evitar "Paypal" vs "paypal"
    platform_name = data.platform.strip().upper()
    details = data.account_details.strip().lower()

    # 2. Verificamos si ya existe exactamente lo mismo para este local
    existing = db.query(ReferralPayoutMethod).filter(
        ReferralPayoutMethod.establishment_id == my_id,
        ReferralPayoutMethod.platform.ilike(platform_name), # Case-insensitive
        ReferralPayoutMethod.account_details.ilike(details) # Case-insensitive
    ).first()

    if existing:
        raise HTTPException(
            status_code=400, 
            detail="payout_method_already_exists"
        )

    try:
        # 3. Si no existe, procedemos a crear
        new_method = ReferralPayoutMethod(
            establishment_id=my_id,
            platform=platform_name, # Guardamos normalizado
            account_details=details # Guardamos normalizado
        )
        
        db.add(new_method)
        db.commit()
        db.refresh(new_method)

        return {
            "status": "success", 
            "message": "Payout method saved",
            "method_id": new_method.id
        }

    except Exception as e:
        db.rollback()
        print(f"❌ Error al guardar método de pago: {e}")
        raise HTTPException(status_code=500, detail="error_saving_payout_method")
    


@router.get("/payout-methods")
def list_payout_methods(
    db: Session = Depends(get_db),
    token_data: dict = Depends(verify_firebase_token) # Extraemos la identidad del Token
):
    """
    Lista los métodos de pago del establecimiento autenticado.
    """
    my_id = token_data.get('uid')
    
    methods = db.query(ReferralPayoutMethod).filter(
        ReferralPayoutMethod.establishment_id == my_id
    ).order_by(ReferralPayoutMethod.created_at.desc()).all()
    
    return methods


@router.delete("/payout-methods/{method_id}")
def delete_payout_method(
    method_id: int, 
    db: Session = Depends(get_db),
    token_data: dict = Depends(verify_firebase_token)
):
    """
    Elimina un método de pago solo si le pertenece al usuario 
    y no tiene retiros asociados.
    """
    my_id = token_data.get('uid')

    # 1. Buscar el método asegurando que pertenezca al establecimiento (Seguridad JWT)
    method = db.query(ReferralPayoutMethod).filter(
        ReferralPayoutMethod.id == method_id,
        ReferralPayoutMethod.establishment_id == my_id
    ).first()

    if not method:
        raise HTTPException(
            status_code=404, 
            detail="payout_method_not_found"
        )

    # 2. Revisar si existen retiros asociados en 'referral_withdrawals'
    # Usamos text() por si no tienes el modelo definido aún o para mayor rapidez
    check_usage = text("""
        SELECT 1 FROM referral_withdrawals 
        WHERE associated_payment_id = :m_id 
        LIMIT 1
    """)
    usage_exists = db.execute(check_usage, {"m_id": method_id}).first()

    if usage_exists:
        raise HTTPException(
            status_code=400, 
            detail="cannot_delete_method_with_active_withdrawals"
        )

    try:
        # 3. Proceder con la eliminación
        db.delete(method)
        db.commit()
        
        return {
            "status": "success", 
            "message": "Method deleted"
        }

    except Exception as e:
        db.rollback()
        print(f"❌ Error al eliminar método: {e}")
        raise HTTPException(status_code=500, detail="error_deleting_payout_method")


@router.post("/withdrawals")
def request_withdrawal(data: WithdrawalRequestCreate, db: Session = Depends(get_db)):
    new_request = ReferralWithdrawal(
        **data.model_dump(),
        status="pending" 
    )
    db.add(new_request)
    db.commit()
    return {"status": "success", "message": "Withdrawal request sent", "withdrawal_status": "pending"}


@router.get("/withdrawals")
def get_withdrawal_history(establishment_id: str, db: Session = Depends(get_db)):
    return db.query(ReferralWithdrawal).filter(
        ReferralWithdrawal.establishment_id == establishment_id
    ).order_by(ReferralWithdrawal.created_at.desc()).all()
    return db.query(ReferidosSolicitudRetiro).filter(
        ReferidosSolicitudRetiro.idestablecimiento == idest
    ).order_by(ReferidosSolicitudRetiro.created_at.desc()).all()


# Lista base de términos prohibidos
BANNED_WORDS = ["wappti", "admin", "support", "soporte", "official", "oficial"]
@router.post("/activate", status_code=201)
def activate_referral_program(
    data: ActivateReferralRequest, 
    request: Request, 
    db: Session = Depends(get_db), 
    token_data: dict = Depends(verify_firebase_token)
):
    uid = token_data.get('uid')
    
    # 1. Normalización y Limpieza
    clean_code = data.requested_code.strip().lower()

    # 2. Validación de formato
    if not clean_code or re.search(r"\s", clean_code) or not re.match(r"^[a-z0-9]+$", clean_code):
        raise HTTPException(status_code=400, detail="error_invalid_format")

    # 3. Lógica Anti-Variantes (Leet Speak)
    visual_normalize = clean_code.translate(str.maketrans('431057', 'aeiost'))
    if any(banned in visual_normalize for banned in BANNED_WORDS):
        raise HTTPException(status_code=400, detail="error_prohibited_word_detected")

    # 4. Obtener estado fresco del establecimiento
    establishment = db.query(Establishment).filter(Establishment.id == uid).first()
    if not establishment:
        raise HTTPException(status_code=404, detail="establishment_not_found")
    
    # --- 5. VALIDACIÓN INTELIGENTE ---
    db_code = (establishment.referral_code or "").strip()
    
    if db_code != "":
        # Si el código ya es el que tiene en la DB, no damos error, devolvemos éxito
        if db_code == clean_code:
            return {
                "status": "success",
                "message": "referral_already_active",
                "code": clean_code
            }
        # Si intenta poner uno nuevo teniendo ya uno activo, bloqueamos
        raise HTTPException(status_code=400, detail="error_user_already_has_code")

    # 6. Disponibilidad Global (que otro negocio no lo tenga)
    code_exists = db.query(ReferralCode).filter(ReferralCode.code == clean_code).first()
    if code_exists:
        raise HTTPException(status_code=400, detail="error_code_already_taken")

    # 7. Ejecución de la activación
    try:
        # Crear el registro en la tabla de códigos
        new_referral = ReferralCode(
            id=uid,
            code=clean_code,
            user_count=0,
            users_list=[]
        )
        db.add(new_referral)
        
        # Vincularlo al establecimiento
        establishment.referral_code = clean_code

        # Registrar Log de Auditoría
        register_action_log(
            db=db,
            establishment_id=uid,
            action="REFERRAL_PROGRAM_ACTIVATED",
            method="POST",
            path=request.url.path,
            payload={"new_code": clean_code},
            request=request
        )

        db.commit()
        
        return {
            "status": "success",
            "message": "referral_activated",
            "code": clean_code
        }

    except Exception as e:
        db.rollback()
        print(f"🚨 DB Error en activación: {e}")
        raise HTTPException(status_code=500, detail="internal_db_error")