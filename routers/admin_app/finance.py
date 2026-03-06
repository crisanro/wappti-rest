import stripe
import os
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from models import Payment, Establishment, UsageAuditLog
from core.database import get_db
from core.auth import verify_app_admin

# Configura tu llave secreta de Stripe (debería estar en tu .env)
stripe.api_key = os.getenv("STRIPE_SECRET_KEY")

router = APIRouter(prefix="/admin/payments", tags=["Admin Payments"])

@router.get("/{payment_intent_id}")
async def get_stripe_payment_details(
    payment_intent_id: str, 
    db: Session = Depends(get_db),
    admin_user: dict = Depends(verify_app_admin)
):
    try:
        # 1. Traemos el pago
        intent = stripe.PaymentIntent.retrieve(
            payment_intent_id,
            expand=['latest_charge']
        )
        
        charge = intent.latest_charge
        customer_name = "Unknown"
        customer_email = "No Email"

        # 2. LÓGICA DE BÚSQUEDA DEL CLIENTE
        # Primero intentamos sacar del charge (billing_details)
        if charge and charge.billing_details and charge.billing_details.email:
            customer_name = charge.billing_details.name or "Unknown"
            customer_email = charge.billing_details.email
        
        # SI SIGUE SIENDO UNKNOWN, buscamos el objeto Customer de Stripe
        elif intent.customer:
            try:
                stripe_customer = stripe.Customer.retrieve(intent.customer)
                # El objeto Customer suele tener 'name' y 'email'
                customer_name = getattr(stripe_customer, 'name', "Unknown") or "Unknown"
                customer_email = getattr(stripe_customer, 'email', "No Email") or "No Email"
            except:
                pass # Si falla la búsqueda del cliente, nos quedamos con los valores por defecto

        # 3. Datos de la tarjeta (mantenemos lo anterior que estaba bien)
        card_details = {}
        if charge and charge.payment_method_details.type == "card":
            card = charge.payment_method_details.card
            card_details = {
                "brand": card.brand.upper(),
                "last4": card.last4,
                "country": card.country,
                "funding": card.funding
            }

        # 4. Cruzar con Wappti Database
        local_payment = db.query(Payment).filter(Payment.id == payment_intent_id).first()
        wappti_name = "Not found in DB"
        if local_payment:
            est = db.query(Establishment.name).filter(Establishment.id == local_payment.establishment_id).first()
            wappti_name = est.name if est else "Unknown Establishment"

        return {
            "payment_id": intent.id,
            "status": intent.status,
            "amount_data": {
                "total": intent.amount / 100.0,
                "currency": intent.currency.upper(),
                "receipt_url": charge.receipt_url if charge else None
            },
            "customer": {
                "name": customer_name,
                "email": customer_email,
                "stripe_customer_id": intent.customer # Agregamos esto para tu referencia
            },
            "wappti_context": {
                "establishment_name": wappti_name
            },
            "payment_method": {
                "type": "card",
                "details": card_details
            },
            "fraud_analysis": {
                "risk_level": charge.outcome.risk_level if charge else "unknown",
                "risk_score": charge.outcome.risk_score if charge else 0
            }
        }

    except stripe.error.StripeError as e:
        raise HTTPException(status_code=400, detail=str(e))