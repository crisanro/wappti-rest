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
from schemas.financials import PaymentCreate, PayoutMethodCreate, WithdrawalRequestCreate, ActivateReferralRequest
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
        
        # 1. Configuración de Zona Horaria
        try:
            local_tz = pytz.timezone(timezone_name)
        except Exception:
            local_tz = pytz.UTC

        def format_dt(dt):
            if not dt: return None
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=pytz.UTC)
            return dt.astimezone(local_tz).isoformat()

        # 2. Obtener Info General y Balance
        referral_info = db.query(ReferralCode).filter(ReferralCode.id == uid).first()
        
        balance_db = db.query(ReferralBalance).filter(
            ReferralBalance.referred_customer_id == uid
        ).order_by(ReferralBalance.created_at.desc()).all()

        # Obtener la fecha de la última recarga de balance (donde amount > 0)
        last_credit = db.query(ReferralBalance.created_at).filter(
            ReferralBalance.referred_customer_id == uid,
            ReferralBalance.amount > 0
        ).order_by(ReferralBalance.created_at.desc()).first()

        # 3. Métodos de Pago
        payout_methods = db.query(ReferralPayoutMethod).filter(
            ReferralPayoutMethod.establishment_id == uid
        ).all()

        # 4. Solicitudes de Retiro
        withdrawals_db = db.query(ReferralWithdrawal).filter(
            ReferralWithdrawal.establishment_id == uid
        ).order_by(ReferralWithdrawal.created_at.desc()).all()

        # --- LÓGICA DE RETIROS MEJORADA ---
        processed_withdrawals = []
        for w in withdrawals_db:
            withdrawal_item = {
                "id": w.id,
                "amount": w.amount,
                "status": w.status,
                "platform": w.platform,
                "account": w.account,
                "created_at": format_dt(w.created_at),
                "payment_date": None,
                "estimated_payment_date": None
            }

            if w.status.lower() in ['pending', 'processing']:
                # Lógica: 5 días después de la última recarga o 48h después de la solicitud (lo que sea mayor)
                base_request_date = w.created_at + timedelta(hours=48)
                
                if last_credit:
                    base_credit_date = last_credit[0] + timedelta(days=5)
                    # Usamos la fecha más lejana (máxima)
                    estimated = max(base_request_date, base_credit_date)
                else:
                    estimated = base_request_date
                
                withdrawal_item["estimated_payment_date"] = format_dt(estimated)
            
            elif w.status.lower() in ['paid', 'completed', 'success']:
                # Si ya está pagado, mostramos la fecha real de pago desde la DB
                # Nota: Asegúrate de tener la columna 'payment_date' en tu tabla 'referral_withdrawals'
                withdrawal_item["payment_date"] = format_dt(w.payment_date)

            processed_withdrawals.append(withdrawal_item)

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
            "withdrawals": processed_withdrawals
        }

    except Exception as e:
        import traceback
        print(f"🚨 ERROR DASHBOARD: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail="error_fetching_dashboard")

"""
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
"""
# --- ACCOUNTS & WITHDRAWALS ---
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
    

@router.delete("/payout-methods/{method_id}")
def delete_payout_method(
    method_id: int, 
    db: Session = Depends(get_db),
    token_data: dict = Depends(verify_firebase_token)
):
    my_id = token_data.get('uid')

    # 1. Buscar el método asegurando propiedad
    method = db.query(ReferralPayoutMethod).filter(
        ReferralPayoutMethod.id == method_id,
        ReferralPayoutMethod.establishment_id == my_id
    ).first()

    if not method:
        raise HTTPException(status_code=404, detail="payout_method_not_found")

    # 2. Verificar si este ID específico ya fue usado en algún retiro
    # Usamos la relación directa por ID
    usage_exists = db.query(ReferralWithdrawal.id).filter(
        ReferralWithdrawal.payout_method_id == method_id
    ).first()

    if usage_exists:
        raise HTTPException(
            status_code=400, 
            detail="cannot_delete_method_used_in_withdrawals"
        )

    try:
        db.delete(method)
        db.commit()
        return {"status": "success", "message": "Method deleted"}

    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail="internal_db_error")
    
@router.delete("/payout-methods/{method_id}")
def delete_payout_method(
    method_id: int, 
    db: Session = Depends(get_db),
    token_data: dict = Depends(verify_firebase_token)
):
    my_id = token_data.get('uid')

    # 1. Buscar el método asegurando propiedad
    method = db.query(ReferralPayoutMethod).filter(
        ReferralPayoutMethod.id == method_id,
        ReferralPayoutMethod.establishment_id == my_id
    ).first()

    if not method:
        raise HTTPException(status_code=404, detail="payout_method_not_found")

    # 2. Verificar si este ID específico ya fue usado en algún retiro
    # Usamos la relación directa por ID
    usage_exists = db.query(ReferralWithdrawal.id).filter(
        ReferralWithdrawal.payout_method_id == method_id
    ).first()

    if usage_exists:
        raise HTTPException(
            status_code=400, 
            detail="cannot_delete_method_used_in_withdrawals"
        )

    try:
        db.delete(method)
        db.commit()
        return {"status": "success", "message": "Method deleted"}

    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail="internal_db_error")
    

@router.post("/withdrawals")
def request_withdrawal(
    data: WithdrawalRequestCreate, 
    db: Session = Depends(get_db),
    token_data: dict = Depends(verify_firebase_token)
):
    # 1. Normalización de seguridad
    establishment_id = token_data.get("uid")
    user_email = str(token_data.get("email", "")).lower().strip()

    try:
        # 2. Validar que el método de pago existe y le pertenece
        payout_method = db.query(ReferralPayoutMethod).filter(
            ReferralPayoutMethod.id == data.payout_method_id,
            ReferralPayoutMethod.establishment_id == establishment_id
        ).first()

        if not payout_method:
            raise HTTPException(status_code=404, detail="payout_method_not_found")

        # 3. Calcular Saldo (Suma total)
        total_balance = db.query(func.sum(ReferralBalance.amount)).filter(
            ReferralBalance.referred_customer_id == establishment_id
        ).scalar() or 0.0

        if data.amount > total_balance or data.amount <= 0:
            raise HTTPException(status_code=400, detail="insufficient_funds_or_invalid_amount")

        # 4. Crear el retiro ASOCIANDO el ID del método
        new_request = ReferralWithdrawal(
            establishment_id=establishment_id,
            payout_method_id=payout_method.id, # <-- Relación directa
            amount=data.amount,
            status="pending",
            # Guardamos snapshot de los datos por si el método se actualiza luego
            platform=payout_method.platform, 
            account=payout_method.account_details,
            created_at=datetime.now(timezone.utc)
        )
        db.add(new_request)

        # 5. Vaciar Balance (Movimiento de ajuste)
        clearing_move = ReferralBalance(
            amount=-total_balance,
            referred_customer_id=establishment_id,
            reference_data=f"Withdrawal ID: {new_request.id}",
            created_at=datetime.now(timezone.utc)
        )
        db.add(clearing_move)

        db.commit()
        return {"status": "success", "withdrawal_id": new_request.id}

    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

"""
@router.get("/withdrawals")
def get_withdrawal_history(establishment_id: str, db: Session = Depends(get_db)):
    return db.query(ReferralWithdrawal).filter(
        ReferralWithdrawal.establishment_id == establishment_id
    ).order_by(ReferralWithdrawal.created_at.desc()).all()
    return db.query(ReferidosSolicitudRetiro).filter(
        ReferidosSolicitudRetiro.idestablecimiento == idest
    ).order_by(ReferidosSolicitudRetiro.created_at.desc()).all()
"""

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
