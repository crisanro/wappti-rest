from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from core.database import get_db
from core.auth import verify_superadmin_key  # Reutilizamos tu validación de seguridad
from models import Establishment, Payment, ReferralBalance, UsageAuditLog, ReferralMKTCampaigns # Tu modelo de base de datos
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
    # 1. IDEMPOTENCY CHECK (409 Conflict si ya existe)
    existing_payment = db.query(Payment).filter(Payment.id == payload.reference_id).first()
    if existing_payment:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, 
            detail="PAYMENT_ALREADY_PROCESSED"
        )

    # 2. VALIDATE ESTABLISHMENT
    payer = db.query(Establishment).filter(Establishment.id == payload.establishment_id).first()
    if not payer:
        raise HTTPException(status_code=404, detail="PAYER_NOT_FOUND")

    try:
        # --- START ATOMIC TRANSACTION ---
        
        # 3. DETERMINE TIER
        payment_seq = db.query(Payment).filter(
            Payment.establishment_id == payer.id,
            Payment.is_refund == False
        ).count() + 1

        # 4. REGISTER MAIN PAYMENT
        new_payment = Payment(
            id=payload.reference_id,
            establishment_id=payer.id,
            amount=payload.amount,
            reason=payload.reason,
            is_refund=False
        )
        db.add(new_payment)
        db.flush()

        # 5. REFERRAL & MARKETING LOGIC
        referral_bonus = 0
        referrer_data = None
        
        if payer.referred_by:
            # Caso Marketing
            if str(payer.referred_by).startswith("CAMP_"):
                camp_id_str = payer.referred_by.replace("CAMP_", "")
                if camp_id_str.isdigit():
                    campaign = db.query(ReferralMKTCampaigns).filter(
                        ReferralMKTCampaigns.id == int(camp_id_str)
                    ).first()
                    if campaign:
                        referrer_data = {"type": "marketing", "id": campaign.id, "name": campaign.name}
            
            # Caso Humano
            else:
                current_rate = 0
                if payment_seq == 1: current_rate = payload.rate_first_pay
                elif payment_seq == 2: current_rate = payload.rate_second_pay
                elif payment_seq == 3: current_rate = payload.rate_third_pay
                
                if current_rate > 0:
                    referrer = db.query(Establishment).filter(Establishment.id == payer.referred_by).first()
                    if referrer:
                        referral_bonus = payload.amount * current_rate
                        last_log = db.query(ReferralBalance).filter(
                            ReferralBalance.referred_customer_id == referrer.id
                        ).order_by(ReferralBalance.id.desc()).first()
                        
                        prev_balance = last_log.balance if last_log else 0.0
                        new_ref_total = prev_balance + referral_bonus
                        
                        ref_log = ReferralBalance(
                            referred_customer_id=referrer.id,
                            amount=referral_bonus,
                            balance=new_ref_total,
                            reference_data=f"Stripe: {payload.reference_id} | From: {payer.id}"
                        )
                        db.add(ref_log)
                        db.flush()
                        new_payment.referral_payment_id = ref_log.id
                        referrer_data = {"type": "human", "id": referrer.id, "bonus": referral_bonus}

        # 6. RECHARGE & AUDIT
        # Guardamos la cantidad previa para mayor claridad si fuera necesario
        recharge_amount = payload.credit_amount
        payer.available_credits = (payer.available_credits or 0) + recharge_amount

        db.add(UsageAuditLog(
            establishment_id=payer.id,
            condition="top-up",
            value=recharge_amount,
            observations=f"Successful recharge via Stripe. Ref: {payload.reference_id}"
        ))

        # 8. COMMIT FINAL
        db.commit()

        # 9. RESPONSE CON DATOS DE RECARGA
        return {
            "status": "success",
            "transaction": {
                "payment_number": payment_seq,
                "stripe_id": payload.reference_id,
                "credits_recharged": recharge_amount  # <--- Aquí tienes el dato que pediste
            },
            "payer": {
                "id": payer.id,
                "language": payer.language or "en",
                "new_balance": payer.available_credits
            },
            "referral_info": referrer_data
        }

    except Exception as e:
        db.rollback()
        print(f"❌ DATABASE TRANSACTION FAILED: {str(e)}")
        # Lanzamos un 400 si la transacción falló por lógica de datos
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, 
            detail=f"TRANSACTION_FAILED: {str(e)}"
        )
