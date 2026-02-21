from sqlalchemy import Column, String, Text, Boolean, DateTime, BigInteger, ForeignKey, Float, func, ARRAY
from sqlalchemy.orm import relationship
from core.database import Base

class Payment(Base):
    """Registro de transacciones (Stripe, etc.)"""
    __tablename__ = "payments"
    
    id = Column(String, primary_key=True) 
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    establishment_id = Column(String, ForeignKey("establishments.id", ondelete="CASCADE"), index=True)
    
    amount = Column(Float)
    reason = Column(Text)
    invoice_link = Column(Text)
    referral_payment_id = Column(BigInteger, nullable=True)
    is_refund = Column(Boolean, default=False)
    refund_id = Column(Text)

class ReferralWithdrawal(Base):
    """Retiros de comisiones (Nombre corregido)"""
    __tablename__ = "referral_withdrawals"
    
    id = Column(BigInteger, primary_key=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    establishment_id = Column(String, index=True)
    
    amount = Column(Float)
    status = Column(Text)
    associated_payment_id = Column(BigInteger, nullable=True)
    payment_date = Column(DateTime(timezone=True))
    platform = Column(Text)
    account = Column(Text)

class ReferralCode(Base):
    """Códigos de invitación"""
    __tablename__ = "referral_codes"
    
    id = Column(String, primary_key=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    code = Column(Text, unique=True, nullable=False)
    user_count = Column(BigInteger, default=0)
    users_list = Column(ARRAY(String), default=[])
    
class ReferralBalance(Base):
    """Balances de comisiones"""
    __tablename__ = "referral_balances"
    
    id = Column(BigInteger, primary_key=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    amount = Column(Float, default=0.0) 
    balance = Column(Float, default=0.0)
    referred_customer_id = Column(String, index=True) 
    reference_data = Column(Text)

class ReferralPayoutMethod(Base):
    """Método de pago (Correo o Número)"""
    __tablename__ = "referral_payout_methods"
    
    id = Column(BigInteger, primary_key=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    establishment_id = Column(String, ForeignKey("establishments.id", ondelete="CASCADE"), index=True)
    platform = Column(Text)         # Ej: "Zelle", "Paypal", "Banco"
    account_details = Column(Text)  # El correo, número o cuenta