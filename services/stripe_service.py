import stripe
import os

stripe.api_key = os.getenv("STRIPE_SECRET_KEY")

class StripeService:
    @staticmethod
    def crear_sesion_suscripcion(customer_id: str, price_id: str, tiene_status: bool):
        # Configuramos los datos de la suscripción
        subscription_data = {}
        
        # Si NO tiene status (está vacío), aplicamos los 3 días de trial
        if not tiene_status:
            subscription_data["trial_period_days"] = 3

        try:
            checkout_session = stripe.checkout.Session.create(
                customer=customer_id,
                success_url="https://wappti.app",
                mode="subscription",
                line_items=[{
                    "price": price_id,
                    "quantity": 1,
                }],
                subscription_data=subscription_data,
            )
            return checkout_session.url
        except Exception as e:
            return str(e)