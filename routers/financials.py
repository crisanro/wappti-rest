import re
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime, timedelta, timezone
import traceback
import pytz
from core.database import get_db
from core.auth import verify_firebase_token
from core.utils import register_action_log
from models import *
# Import English models
from schemas.financials import PaymentCreate, PayoutMethodCreate, WithdrawalRequestCreate, StripeCheckoutSchema
from services.stripe_service import StripeService

router = APIRouter(dependencies=[Depends(verify_firebase_token)])

# --- STRIPE CHECKOUT SECTION ---

@router.get("/")
def get_payment_history(
    tz_name: str = "America/Guayaquil",
    db: Session = Depends(get_db), 
    token_data: dict = Depends(verify_firebase_token)
):
    try:
        establishment_id = token_data.get('uid')

        # 1. Zona Horaria
        try:
            user_tz = pytz.timezone(tz_name)
        except Exception:
            user_tz = pytz.UTC

        # 2. Consulta
        payments_db = db.query(Payment).filter(
            Payment.establishment_id == establishment_id
        ).order_by(Payment.created_at.desc()).all()

        # 3. Transformación con los campos exactos que necesitas
        result = []
        for p in payments_db:
            try:
                # Procesar fecha
                created_at_utc = p.created_at
                if created_at_utc.tzinfo is None:
                    created_at_utc = created_at_utc.replace(tzinfo=pytz.UTC)
                
                local_date = created_at_utc.astimezone(user_tz)

                result.append({
                    "id": getattr(p, "id", None),
                    "amount": getattr(p, "amount", 0.0),
                    "reason": getattr(p, "reason", "Sin descripción"), # Campo solicitado
                    "id_refund": getattr(p, "id_refund", None),         # Campo solicitado
                    "created_at": local_date.isoformat()
                })
            except Exception as inner_e:
                print(f"⚠️ Error procesando pago {getattr(p, 'id', 'unknown')}: {inner_e}")
                continue
        
        return result

    except Exception as e:
        print("--- ERROR EN HISTORIAL DE PAGOS ---")
        print(traceback.format_exc()) 
        raise HTTPException(status_code=500, detail="error_processing_payments")
    

@router.post("/")
def register_payment(data: PaymentCreate, db: Session = Depends(get_db)):
    new_payment = Payment(**data.model_dump())
    db.add(new_payment)
    db.commit()
    db.refresh(new_payment)
    
    register_action_log(db, data.establishment_id, "REGISTER_PAYMENT", "POST", "/payments/register", data.model_dump())
    
    return {"status": "success", "id": new_payment.id}



@router.post("/checkout")
async def create_checkout(data: StripeCheckoutSchema):
    """
    Logic: If 'status' is empty/None, apply a 3-day trial.
    """
    has_history = bool(data.status and data.status.strip())
    
    url = StripeService.crear_sesion_suscripcion(
        customer_id=data.customer_id,
        price_id=data.price_id,
        tiene_status=has_history
    )
    
    if not url.startswith("http"):
        raise HTTPException(status_code=400, detail=f"Stripe Error: {url}")
        
    return {"status": "success", "url": url}


# --- PAYMENTS SECTION ---



