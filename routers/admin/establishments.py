from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from core.database import get_db
from core.auth import verify_superadmin_key  # Reutilizamos tu validación de seguridad
from models import Establishment, Payment, ReferralBalance, AppNotification              # Tu modelo de base de datos
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
    # 1. Validate establishment existence
    establishment = db.query(Establishment).filter(Establishment.id == payload.establishment_id).first()
    if not establishment:
        raise HTTPException(status_code=404, detail="ESTABLISHMENT_NOT_FOUND")

    try:
        # --- DATABASE TRANSACTION START ---
        
        # 2. Check payment sequence (excluding refunds)
        previous_payments_count = db.query(Payment).filter(
            Payment.establishment_id == establishment.id,
            Payment.is_refund == False
        ).count()

        # 3. Create Main Payment Record
        new_payment = Payment(
            id=payload.reference_id,
            establishment_id=establishment.id,
            amount=payload.amount,
            reason="Credit Recharge",
            invoice_link=payload.invoice_link,
            is_refund=False
        )
        db.add(new_payment)
        db.flush() 

        # 4. Referral Tier Logic
        referral_bonus = 0
        current_rate = 0
        referrer_id = None
        
        if establishment.referred_by:
            # Select rate based on history
            if previous_payments_count == 0:
                current_rate = payload.rate_first_pay
            elif previous_payments_count == 1:
                current_rate = payload.rate_second_pay
            elif previous_payments_count == 2:
                current_rate = payload.rate_third_pay
            
            if current_rate > 0:
                referrer = db.query(Establishment).filter(Establishment.id == establishment.referred_by).first()
                if referrer:
                    referrer_id = referrer.id
                    referral_bonus = payload.amount * current_rate
                    
                    # Update Referrer Balance
                    referrer.available_credits = (referrer.available_credits or 0) + referral_bonus
                    
                    # Log Referral Balance Earning
                    ref_log = ReferralBalance(
                        amount=referral_bonus,
                        balance=referrer.available_credits,
                        referred_customer_id=establishment.id,
                        reference_data=f"Tier {int(current_rate*100)}% - Pay #{previous_payments_count + 1}"
                    )
                    db.add(ref_log)
                    db.flush()
                    
                    # Link Payment with the earning log
                    new_payment.referral_payment_id = ref_log.id

        # 5. Update Payer Balance (Credits)
        new_establishment_balance = (establishment.available_credits or 0) + payload.amount
        establishment.available_credits = new_establishment_balance

        # 6. ATOMIC COMMIT
        db.commit()

        # 7. Response Data (Use this to trigger notifications in n8n)
        return {
            "status": "success",
            "payment_sequence": previous_payments_count + 1,
            "applied_rate": f"{int(current_rate*100)}%",
            "bonus_earned": referral_bonus,
            "referrer_id": referrer_id,
            "payer_id": establishment.id,
            "payer_email": establishment.email,
            "payer_new_balance": new_establishment_balance,
            "payer_language": establishment.language # Useful for your external notifications
        }

    except Exception as e:
        db.rollback()
        print(f"❌ Transaction Failed: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
            detail="DATABASE_TRANSACTION_FAILED"
        )
