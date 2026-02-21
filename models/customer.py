from sqlalchemy import Column, String, Text, Boolean, DateTime, BigInteger, Integer, ForeignKey, Float, func, ARRAY,Numeric
from sqlalchemy.orm import relationship
from core.database import Base

class Customer(Base):
    """Clientes de los locales (Antes WTUsuarios)"""
    __tablename__ = "customers"
    
    id = Column(BigInteger, primary_key=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    establishment_id = Column(String, ForeignKey("establishments.id", ondelete="CASCADE"), index=True)
    
    # Esta columna estaba en el SQL y te faltaba:
    profile_id = Column(BigInteger, nullable=True) 
    
    first_name = Column(Text)
    last_name = Column(Text)
    phone = Column(BigInteger)
    country_code = Column(BigInteger) # En el SQL es bigint
    country_name = Column(Text)       # Nueva según SQL
    email = Column(Text)
    identification_id = Column(Text)
    last_visit = Column(DateTime(timezone=True))
    is_active = Column(Boolean, default=True)
    notes = Column(Text)
    tag_ids = Column(ARRAY(Integer), default=[])
    language = Column(Text)

    # Relaciones
    establishment = relationship("Establishment", back_populates="customers")
    appointments = relationship("Appointment", back_populates="customer", cascade="all, delete-orphan")
    history = relationship("CustomerHistory", back_populates="customer", cascade="all, delete-orphan")
    establishment = relationship("Establishment", back_populates="customers")

class CustomerTag(Base):
    """Etiquetas para segmentar clientes (Antes WTTags)"""
    __tablename__ = "customer_tags"
    
    id = Column(BigInteger, primary_key=True, index=True)
    establishment_id = Column(String, ForeignKey("establishments.id", ondelete="CASCADE"), index=True)
    name = Column(Text, nullable=False)
    total_customers = Column(BigInteger, default=0) # SQL dice BigInt
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class CustomerHistory(Base):
    """Ventas e Ingresos (Antes WTProcesos)"""
    __tablename__ = "customer_history"
    
    id = Column(BigInteger, primary_key=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    establishment_id = Column(Text, ForeignKey("establishments.id", ondelete="CASCADE"), index=True)
    customer_id = Column(BigInteger, ForeignKey("customers.id", ondelete="CASCADE"), index=True)
    profile_id = Column(BigInteger, nullable=True) 
    
    process_name = Column(Text)
    income = Column(Float, default=0.0) # double precision en SQL
    notes = Column(Text)

    customer = relationship("Customer", back_populates="history")

class CustomerFeedback(Base):
    """
    Feedback y quejas de clientes.
    Nota: Según tu SQL, se vincula por 'establishment_signature'
    """
    __tablename__ = "customer_feedback"
    
    id = Column(BigInteger, primary_key=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    complaint = Column(Text)
    # En tu SQL no hay FK aquí, se usa una firma de texto
    establishment_signature = Column(Text)



class CustomerPlan(Base):
    __tablename__ = "customer_plans"

    id = Column(Integer, primary_key=True, index=True)
    customer_id = Column(Integer, ForeignKey("customers.id", ondelete="CASCADE"), nullable=False)
    establishment_id = Column(Text, ForeignKey("establishments.id"), nullable=False)
    title = Column(String(255), nullable=False)
    general_notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relaciones
    # Permite hacer: plan.items para ver todos los rubros
    items = relationship("CustomerPlanItem", back_populates="plan", cascade="all, delete-orphan")
    # Si tienes el modelo Customer definido:
    # customer = relationship("Customer")

class CustomerPlanItem(Base):
    __tablename__ = "customer_plan_items"

    id = Column(Integer, primary_key=True, index=True)
    plan_id = Column(Integer, ForeignKey("customer_plans.id", ondelete="CASCADE"), nullable=False)
    description = Column(Text, nullable=False)
    # Usamos Numeric para precisión de dinero (10 dígitos, 2 decimales)
    amount = Column(Numeric(10, 2), nullable=False, default=0.00)
    is_completed = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relación inversa
    plan = relationship("CustomerPlan", back_populates="items")



class CustomerDebt(Base):
    __tablename__ = "customer_debts"

    id = Column(Integer, primary_key=True, index=True)
    customer_id = Column(Integer, ForeignKey("customers.id", ondelete="CASCADE"), nullable=False)
    establishment_id = Column(Text, ForeignKey("establishments.id"), nullable=False)
    title = Column(String(255), nullable=False)
    total_amount = Column(Numeric(10, 2), nullable=False, default=0.00)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relación para traer los abonos fácilmente
    payments = relationship("CustomerPayment", back_populates="debt", cascade="all, delete-orphan")

class CustomerPayment(Base):
    __tablename__ = "customer_payments"

    id = Column(Integer, primary_key=True, index=True)
    debt_id = Column(Integer, ForeignKey("customer_debts.id", ondelete="CASCADE"), nullable=False)
    amount = Column(Numeric(10, 2), nullable=False, default=0.00)
    payment_method = Column(String(50), nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    debt = relationship("CustomerDebt", back_populates="payments")