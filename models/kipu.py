from sqlalchemy import Column, String, Text, Boolean, DateTime, BigInteger, ForeignKey, Float, func, Integer
from sqlalchemy.orm import relationship
from core.database import Base


class CustomerBillingProfile(Base):
    __tablename__ = "customer_billing_profiles"

    id = Column(Integer, primary_key=True, index=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False)
    establishment_id = Column(Text, nullable=False)
    tax_id_type = Column(String(50), nullable=False)
    tax_id_number = Column(String(25), nullable=False)
    business_name = Column(String(255), nullable=False)
    created_at = Column(DateTime, server_default=func.now())