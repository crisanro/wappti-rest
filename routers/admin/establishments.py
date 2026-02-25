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


@router.post("/process-transaction")
def process_full_transaction(payload: GlobalPaymentProcessor, db: Session = Depends(get_db)):
    # 1. Retrieve the Payer (Establishment purchasing credits)
    payer = db.query(Establishment).filter(Establishment.id == payload.establishment_id).first()
    if not payer:
        raise HTTPException(status_code=404, detail="PAYER_NOT_FOUND")

    try:
        # --- DATABASE TRANSACTION START ---
        
        # 2. Count successful previous payments to define the current tier
        payment_seq = db.query(Payment).filter(
            Payment.establishment_id == payer.id,
            Payment.is_refund == False
        ).count() + 1

        # 3. Create the Main Payment Record
        new_payment = Payment(
            id=payload.reference_id,
            establishment_id=payer.id,
            amount=payload.amount,
            reason=f"Credit Purchase - Pay #{payment_seq}",
            is_refund=False
        )
        db.add(new_payment)
        db.flush() 

        # 4. Tiered Referral Commission Logic
        referral_bonus = 0
        current_rate = 0
        referrer_data = None
        
        if payer.referred_by:
            # Select rate based on the current sequence
            if payment_seq == 1:
                current_rate = payload.rate_first_pay
            elif payment_seq == 2:
                current_rate = payload.rate_second_pay
            elif payment_seq == 3:
                current_rate = payload.rate_third_pay
            
            if current_rate > 0:
                # Find the Referrer (The one earning the commission)
                referrer = db.query(Establishment).filter(Establishment.id == payer.referred_by).first()
                if referrer:
                    referral_bonus = payload.amount * current_rate
                    
                    # CUMULATIVE BALANCE LOGIC:
                    # Get the most recent balance entry for the referrer
                    last_log = db.query(ReferralBalance).filter(
                        ReferralBalance.establishment_id == referrer.id
                    ).order_by(ReferralBalance.id.desc()).first()
                    
                    previous_balance = last_log.balance if last_log else 0.0
                    new_ref_total = previous_balance + referral_bonus
                    
                    # Update Referrer's primary balance field
                    referrer.available_credits = (referrer.available_credits or 0) + referral_bonus
                    
                    # Create the Audit Row for the Referrer
                    ref_log = ReferralBalance(
                        establishment_id=referrer.id,  
                        amount=referral_bonus,         
                        balance=new_ref_total,         
                        referred_customer_id=payer.id, # FIXED: Using ID instead of Email
                        reference_data=f"Stripe ID: {payload.reference_id} | Tier: {int(current_rate*100)}%"
                    )
                    db.add(ref_log)
                    db.flush()
                    
                    # Cross-reference the payment with the commission log
                    new_payment.referral_payment_id = ref_log.id
                    
                    referrer_data = {
                        "id": referrer.id,
                        "email": referrer.email,
                        "language": referrer.language or "en",
                        "bonus_earned": referral_bonus,
                        "new_balance": new_ref_total
                    }

        # 5. Credits fulfillment for the Payer
        payer.available_credits = (payer.available_credits or 0) + payload.credit_amount

        # 6. ATOMIC COMMIT (Save everything or nothing)
        db.commit()

        # 7. Final response for n8n notification triggering
        return {
            "status": "success",
            "transaction_details": {
                "stripe_id": payload.reference_id,
                "tier_applied": f"{int(current_rate*100)}%",
                "payment_number": payment_seq,
                "credits_added": payload.credit_amount
            },
            "payer_info": {
                "id": payer.id,
                "email": payer.email,
                "language": payer.language or "en"
            },
            "referral_info": {
                "rewarded": referral_bonus > 0,
                "data": referrer_data
            }
        }

    except Exception as e:
        db.rollback()
        print(f"❌ DATABASE TRANSACTION FAILED: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
            detail="INTERNAL_LEDGER_ERROR"
        )
