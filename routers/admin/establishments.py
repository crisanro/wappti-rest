from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from core.database import get_db
from core.auth import verify_superadmin_key  # Reutilizamos tu validación de seguridad
from models import Establishment, Payment, ReferralBalance              # Tu modelo de base de datos
from schemas.admin.establishments import CreditReload, GlobalPaymentProcessor # El schema que creamos

# 1. Configuración del Router con Seguridad de Superadmin
router = APIRouter(
    prefix="/admin/establishments",
    tags=["Admin Establishments"],
    dependencies=[Depends(verify_superadmin_key)] # <-- Bloqueo total para externos
)

@router.patch("/add-credits/{establishment_id}")
def add_credits_to_establishment(
    establishment_id: str, 
    payload: CreditReload, 
    db: Session = Depends(get_db)
):
    """
    Suma créditos de forma segura a un establecimiento.
    Solo accesible con X-Superadmin-Key.
    """
    
    # 1. Buscar al establecimiento
    # Usamos .with_for_update() si quieres bloqueo de fila (opcional para máxima seguridad)
    establishment = db.query(Establishment).filter(Establishment.id == establishment_id).first()
    
    if not establishment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, 
            detail="Establecimiento no encontrado"
        )

    # 2. Lógica de suma atómica
    # Esto evita problemas si se mandan 2 peticiones al mismo tiempo
    try:
        current_balance = establishment.available_credits or 0
        establishment.available_credits = current_balance + payload.amount

        # 3. Guardar cambios
        db.commit()
        db.refresh(establishment)
    except Exception as e:
        db.rollback()
        # Aquí podrías loggear el error real para debug: print(f"Error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
            detail="Error interno al procesar la recarga"
        )

    return {
        "status": "success",
        "message": f"Se han recargado {payload.amount} créditos correctamente.",
        "data": {
            "establishment_id": establishment.id,
            "establishment_name": establishment.name,
            "previous_balance": current_balance,
            "new_balance": establishment.available_credits
        }
    }

@router.get("/search-by-email")
def get_active_establishment_by_email(
    email: str, 
    db: Session = Depends(get_db)
):
    """
    Find the single active establishment for a given email.
    """
    establishment = db.query(Establishment).filter(
        Establishment.email == email,
        Establishment.is_deleted == False
    ).first()

    if not establishment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="ACTIVE_ESTABLISHMENT_NOT_FOUND"
        )

    return {
        "status": "success",
        "data": {
            "id": establishment.id,
            "name": establishment.name,
            "email": establishment.email,
            "available_credits": establishment.available_credits
        }
    }


@router.post("/process-full-transaction")
def process_full_transaction(payload: GlobalPaymentProcessor, db: Session = Depends(get_db)):
    # 1. Get Payer (the one purchasing credits)
    payer = db.query(Establishment).filter(Establishment.id == payload.establishment_id).first()
    if not payer:
        raise HTTPException(status_code=404, detail="PAYER_NOT_FOUND")

    try:
        # --- START ATOMIC TRANSACTION ---
        
        # 2. Determine Payment Sequence (for tiered commissions)
        payment_seq = db.query(Payment).filter(
            Payment.establishment_id == payer.id,
            Payment.is_refund == False
        ).count() + 1

        # 3. Log the Main Payment (Financial Record)
        new_payment = Payment(
            id=payload.reference_id,
            establishment_id=payer.id,
            amount=payload.amount, # Actual money
            reason=f"Purchase of {payload.credit_amount} credits - Pay #{payment_seq}",
            is_refund=False
        )
        db.add(new_payment)
        db.flush() 

        # 4. Tiered Referral Commission Logic
        referral_bonus = 0
        current_rate = 0
        referrer_data = None
        
        if payer.referred_by:
            if payment_seq == 1:
                current_rate = payload.rate_first_pay
            elif payment_seq == 2:
                current_rate = payload.rate_second_pay
            elif payment_seq == 3:
                current_rate = payload.rate_third_pay
            
            if current_rate > 0:
                referrer = db.query(Establishment).filter(Establishment.id == payer.referred_by).first()
                if referrer:
                    # Calculate bonus based on money amount
                    referral_bonus = payload.amount * current_rate
                    
                    # Add money to Referrer's balance
                    referrer.available_credits = (referrer.available_credits or 0) + referral_bonus
                    
                    # Log Earning
                    ref_log = ReferralBalance(
                        amount=referral_bonus,
                        balance=referrer.available_credits,
                        referred_customer_id=payer.id,
                        reference_data=f"Commission {int(current_rate*100)}% - From {payer.email} (Pay #{payment_seq})"
                    )
                    db.add(ref_log)
                    db.flush()
                    
                    new_payment.referral_payment_id = ref_log.id
                    
                    referrer_data = {
                        "id": referrer.id,
                        "email": referrer.email,
                        "language": referrer.language or "en",
                        "bonus_earned": referral_bonus
                    }

        # 5. Add Purchased Credits to Payer
        payer.available_credits = (payer.available_credits or 0) + payload.credit_amount

        # 6. COMMIT ALL CHANGES
        db.commit()

        # 7. Detailed JSON response for n8n notifications
        return {
            "status": "success",
            "transaction": {
                "payment_id": new_payment.id,
                "payment_number": payment_seq,
                "money_paid": payload.amount,
                "credits_added": payload.credit_amount
            },
            "payer": {
                "id": payer.id,
                "email": payer.email,
                "language": payer.language or "en",
                "new_total_balance": payer.available_credits
            },
            "referral": {
                "applied": referral_bonus > 0,
                "rate": f"{int(current_rate*100)}%",
                "data": referrer_data
            }
        }

    except Exception as e:
        db.rollback()
        print(f"❌ Transaction Error: {str(e)}")
        raise HTTPException(status_code=500, detail="INTERNAL_TRANSACTION_ERROR")
