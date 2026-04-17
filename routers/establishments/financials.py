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
from schemas.financials import PaymentCreate, PayoutMethodCreate, WithdrawalRequestCreate, StripeCheckoutRequest
from services.stripe_service import StripeService
import stripe
from core.config import settings
from firebase_admin import firestore
from services.email_service import generate_invoice_pdf, send_invoice_email

# 1. Configuración de Stripe y Firestore
stripe.api_key = settings.STRIPE_SECRET_KEY
db_firestore = firestore.client()

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


# ==========================================
# 2. ENDPOINT: LISTAR PRECIOS
# ==========================================
@router.get("/prices")
def get_subscription_prices():
    prices_data = []
    try:
        for price_id in settings.STRIPE_PRICE_IDS:
            price = stripe.Price.retrieve(price_id, expand=['product'])
            
            prices_data.append({
                "price_id": price.id,
                # Stripe maneja centavos, dividimos para tener los 2 decimales
                "amount": price.unit_amount / 100.0, 
                "currency": price.currency.upper(),
                "product_name": price.product.name if hasattr(price, 'product') else "Plan"
            })
            
        # Ordenamos la lista de menor a mayor basándonos en el 'amount'
        prices_data.sort(key=lambda x: x["amount"])
            
        return {"prices": prices_data}
    except stripe.error.StripeError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ==========================================
# 3. ENDPOINT: CREAR CHECKOUT
# ==========================================
@router.post("/checkout")
def create_checkout(
    data: StripeCheckoutRequest, 
    token_data: dict = Depends(verify_firebase_token)
):
    email = token_data.get("email")
    uid = token_data.get("uid")
    
    if not email or not uid:
        raise HTTPException(status_code=400, detail="El token es inválido o no contiene email/uid.")
        
    try:
        # A. Verificar en Firestore si ya ha sido suscriptor
        user_ref = db_firestore.collection("users").document(uid)
        user_doc = user_ref.get()
        
        has_history = False
        if user_doc.exists:
            user_data = user_doc.to_dict()
            sub_status = user_data.get("SubStatusStripe")
            if sub_status and str(sub_status).strip():
                has_history = True

        # B. Buscar o crear el cliente en Stripe
        customers = stripe.Customer.list(email=email, limit=1)
        if customers.data:
            customer_id = customers.data[0].id
        else:
            new_customer = stripe.Customer.create(email=email)
            customer_id = new_customer.id

        # C. Configurar los parámetros del Checkout
        checkout_params = {
            "customer": customer_id,
            "payment_method_types": ['card'],
            "line_items": [{'price': data.price_id, 'quantity': 1}],
            "mode": 'subscription',
            "success_url": "https://tu-app.com/success?session_id={CHECKOUT_SESSION_ID}", # ⚠️ RECUERDA CAMBIAR ESTA URL
            "cancel_url": "https://tu-app.com/cancel", # ⚠️ RECUERDA CAMBIAR ESTA URL
        }

        # D. Asignar los 7 días de prueba solo si NUNCA ha tenido suscripción
        if not has_history:
            checkout_params["subscription_data"] = {
                "trial_period_days": 7
            }

        # E. Generar el Checkout y retornar la URL
        session = stripe.checkout.Session.create(**checkout_params)
        return {"status": "success", "url": session.url}

    except stripe.error.StripeError as e:
        raise HTTPException(status_code=400, detail=f"Stripe Error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error interno: {str(e)}")
    

# ==========================================
# OBTENER DETALLES (SEGURO POR EMAIL)
# ==========================================
@router.get("/payment-intents/{intent_id}")
def get_payment_intent(
    intent_id: str,
    token_data: dict = Depends(verify_firebase_token)
):
    # Normalizamos el email del JWT a minúsculas
    user_email = str(token_data.get("email", "")).lower().strip()
    
    try:
        intent = stripe.PaymentIntent.retrieve(
            intent_id,
            expand=['latest_charge', 'customer']
        )

        # 1. SEGURIDAD: Extraer y normalizar email de Stripe
        stripe_email = None
        if intent.customer and hasattr(intent.customer, 'email'):
            stripe_email = intent.customer.email
        elif intent.latest_charge and intent.latest_charge.billing_details:
            stripe_email = intent.latest_charge.billing_details.email

        # Normalización crítica para la comparación
        if stripe_email:
            stripe_email = stripe_email.lower().strip()

        if stripe_email != user_email:
            raise HTTPException(status_code=403, detail="No tienes permiso para ver este pago.")

        # 2. Extraer los datos solicitados
        charge = intent.latest_charge
        receipt_url = charge.receipt_url if charge and hasattr(charge, 'receipt_url') else None
        
        card_info = {}
        if charge and hasattr(charge, 'payment_method_details') and charge.payment_method_details.type == "card":
            card = charge.payment_method_details.card
            card_info = {
                "brand": card.brand.upper() if card.brand else "UNKNOWN",
                "last4": card.last4,
                "funding": card.funding,
                "country": card.country
            }

        return {
            "id": intent.id,
            "status": intent.status,
            "amount": intent.amount / 100.0,
            "receipt_url": receipt_url,
            "card_info": card_info
        }

    except stripe.error.StripeError as e:
        raise HTTPException(status_code=400, detail=str(e))

# ==========================================
# SOLICITAR FACTURA POR CORREO
# ==========================================
@router.post("/payment-intents/{intent_id}/send-invoice")
def request_invoice(
    intent_id: str,
    token_data: dict = Depends(verify_firebase_token)
):
    # Normalizamos el email del JWT a minúsculas
    user_email = str(token_data.get("email", "")).lower().strip()
    now_utc = datetime.now(timezone.utc)
    
    try:
        # 1. ANTI-SPAM: Validar Rate Limit
        rate_limit_ref = db_firestore.collection("invoice_rate_limits").document(intent_id)
        rate_limit_doc = rate_limit_ref.get()
        
        if rate_limit_doc.exists:
            last_sent = rate_limit_doc.to_dict().get("last_sent_at")
            if last_sent:
                time_diff = now_utc - last_sent
                if time_diff < timedelta(hours=1):
                    minutes_left = 60 - int(time_diff.total_seconds() / 60)
                    raise HTTPException(
                        status_code=status.HTTP_429_TOO_MANY_REQUESTS, 
                        detail=f"Wait {minutes_left} minutes before requesting this invoice again."
                    )

        # 2. SEGURIDAD: Normalizar email de Stripe antes de comparar
        intent = stripe.PaymentIntent.retrieve(intent_id, expand=['customer'])
        
        stripe_email = intent.customer.email if intent.customer else None
        if stripe_email:
            stripe_email = stripe_email.lower().strip()

        if stripe_email != user_email:
            raise HTTPException(status_code=403, detail="You do not have permission to access this invoice.")

        # 3. PROCESO DE ENVÍO
        invoice_data = {
            "invoice_number": intent_id[-10:].upper(),
            "date": datetime.fromtimestamp(intent.created).strftime('%B %d, %Y'),
            "customer_email": user_email, # Ya está en minúsculas
            "amount": intent.amount / 100.0,
            "description": "Wappti App Subscription Service"
        }

        pdf_bytes = generate_invoice_pdf(invoice_data)
        success = send_invoice_email(user_email, pdf_bytes, invoice_data["invoice_number"])

        if not success:
            raise HTTPException(status_code=500, detail="Failed to send email. Please try again later.")

        # 4. REGISTRAR ÉXITO
        rate_limit_ref.set({
            "last_sent_at": now_utc,
            "user_id": token_data.get("uid"),
            "email": user_email # Guardamos en minúsculas para consistencia
        })

        return {"status": "success", "message": f"Invoice sent to {user_email}"}

    except stripe.error.StripeError as e:
        raise HTTPException(status_code=400, detail=str(e))