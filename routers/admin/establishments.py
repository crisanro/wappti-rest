from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import desc
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
def get_latest_active_establishment_by_email(
    email: str, 
    db: Session = Depends(get_db)
):
    """
    Busca el establecimiento activo más reciente para un correo dado,
    ordenado por fecha de creación descendente.
    """
    # Limpiamos el email por si viene con espacios
    clean_email = email.strip().lower()

    establishment = db.query(Establishment).filter(
        Establishment.email == clean_email,
        Establishment.is_deleted == False
    ).order_by(
        desc(Establishment.created_at) # Ordenamos: del más nuevo al más viejo
    ).first() # Tomamos el primero de esa lista (el más reciente)

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
            "available_credits": establishment.available_credits,
            "created_at": establishment.created_at
        }
    }
    

@router.post("/process-transaction")
def process_full_transaction(payload: GlobalPaymentProcessor, db: Session = Depends(get_db)):
    # 1. IDEMPOTENCY CHECK
    existing_payment = db.query(Payment).filter(Payment.id == payload.reference_id).first()
    if existing_payment:
        return {"status": "already_processed", "transaction_details": {"stripe_id": existing_payment.id}}

    # 2. VALIDATE ESTABLISHMENT
    payer = db.query(Establishment).filter(Establishment.id == payload.establishment_id).first()
    if not payer:
        raise HTTPException(status_code=404, detail="PAYER_NOT_FOUND")

    try:
        # --- START ATOMIC TRANSACTION ---
        
        # 3. DETERMINE TIER (Basado en historial de pagos exitosos)
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
            # CASO A: Es una Campaña de Marketing (ID empieza con CAMP_)
            if str(payer.referred_by).startswith("CAMP_"):
                camp_id = int(payer.referred_by.replace("CAMP_", ""))
                campaign = db.query(ReferralMKTCampaigns).filter(ReferralMKTCampaigns.id == camp_id).first()
                if campaign:
                    # Aquí podrías aplicar lógica extra si la campaña da descuentos 
                    # o créditos extra por cada compra, no solo al inicio.
                    referrer_data = {"type": "marketing", "id": campaign.id, "name": campaign.name}

            # CASO B: Es un Referido Humano
            else:
                current_rate = 0
                if payment_seq == 1: current_rate = payload.rate_first_pay
                elif payment_seq == 2: current_rate = payload.rate_second_pay
                elif payment_seq == 3: current_rate = payload.rate_third_pay
                
                if current_rate > 0:
                    referrer = db.query(Establishment).filter(Establishment.id == payer.referred_by).first()
                    if referrer:
                        referral_bonus = payload.amount * current_rate
                        
                        # Actualizar Balance Acumulado del Referente (Cashback/Comisión)
                        last_log = db.query(ReferralBalance).filter(
                            ReferralBalance.referred_customer_id == referrer.id
                        ).order_by(ReferralBalance.id.desc()).first()
                        
                        prev_balance = last_log.balance if last_log else 0.0
                        new_ref_total = prev_balance + referral_bonus
                        
                        # Update Referrer (Aquí podrías decidir si le das créditos o dinero)
                        # referrer.available_credits += referral_bonus 
                        
                        ref_log = ReferralBalance(
                            referred_customer_id=referrer.id,
                            amount=referral_bonus,
                            balance=new_ref_total,
                            reference_data=f"Stripe: {payload.reference_id} | From: {payer.id}"
                        )
                        db.add(ref_log)
                        db.flush()
                        new_payment.referral_payment_id = ref_log.id
                        
                        referrer_data = {
                            "type": "human",
                            "id": referrer.id,
                            "bonus": referral_bonus
                        }

        # 6. RECHARGE CREDITS TO PAYER
        payer.available_credits = (payer.available_credits or 0) + payload.credit_amount

        # 7. USAGE AUDIT LOG
        audit_log = UsageAuditLog(
            establishment_id=payer.id,
            condition="top-up",
            value=payload.credit_amount,
            observations=f"Successful recharge via Stripe. Ref: {payload.reference_id}"
        )
        db.add(audit_log)

        # 8. COMMIT
        db.commit()

        return {
            "status": "success",
            "transaction": {"payment_number": payment_seq, "stripe_id": payload.reference_id},
            "payer": {"id": payer.id, "new_balance": payer.available_credits},
            "referral_info": referrer_data
        }

    except Exception as e:
        db.rollback()
        print(f"❌ DATABASE TRANSACTION FAILED: {str(e)}")
        raise HTTPException(status_code=500, detail="INTERNAL_SERVER_ERROR")
