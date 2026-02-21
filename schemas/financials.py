from pydantic import BaseModel, Field
from typing import Optional, List, Dict
from datetime import datetime

# --- 1. PAGOS (Ingresos por Stripe) ---

class PaymentCreate(BaseModel):
    """Esquema para registrar un nuevo pago (Faltaba en tu código)"""
    amount: float
    currency: str = "USD"
    description: Optional[str] = None
    stripe_payment_intent_id: str

class StripeCheckoutSchema(BaseModel):
    """Esquema para iniciar sesión de checkout (Faltaba en tu código)"""
    price_id: str
    success_url: str
    cancel_url: str

class PaymentResponse(BaseModel):
    id: int
    created_at: datetime
    amount: float
    currency: str
    status: str          # ej: "succeeded", "pending"
    description: str     # ej: "Pack 100 Créditos"
    payment_method: str  # ej: "card"

    class Config:
        from_attributes = True


# --- 2. PROGRAMA DE REFERIDOS ---

class ReferralCodeBase(BaseModel):
    id: str = Field(..., description="El código de referido único")
    is_active: bool = True

class ReferralBalanceResponse(BaseModel):
    establishment_id: str
    total_earned: float
    current_balance: float
    pending_balance: float
    updated_at: datetime

    class Config:
        from_attributes = True

class ActivateReferralRequest(BaseModel):
    requested_code: str
    
# --- 3. RETIROS ---

class PayoutMethodCreate(BaseModel):
    platform: str = Field(..., example="Paypal")
    account_details: str = Field(..., example="usuario@email.com")

class WithdrawalRequestCreate(BaseModel):
    amount: float = Field(..., gt=0)
    payout_method_id: int

class WithdrawalResponse(BaseModel):
    id: int
    created_at: datetime
    amount: float
    status: str             # ej: "requested", "completed", "rejected"
    processed_at: Optional[datetime] = None
    admin_notes: Optional[str] = None

    class Config:
        from_attributes = True


class DebtCreate(BaseModel):
    customer_id: int
    title: str
    total_amount: float
    notes: Optional[str] = None

# Para registrar un abono
class PaymentCreate(BaseModel):
    debt_id: int
    amount: float
    payment_method: str # Ej: "Efectivo", "Transferencia"
    notes: Optional[str] = None