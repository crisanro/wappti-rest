from sqlalchemy import Column, String, Text, Boolean, DateTime, BigInteger, ForeignKey, Float, func, ARRAY
from sqlalchemy.orm import relationship
from core.database import Base

class Establishment(Base):
    """Dueños del negocio (Antes WTEstablecimientos)"""
    __tablename__ = "establishments"
    
    id = Column(String, primary_key=True) # Firebase UID
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    name = Column(Text)
    email = Column(Text)
    country = Column(Text)
    whatsapp = Column(Text)
    message_signature = Column(Text)
    contact_card = Column(Text)
    is_suspended = Column(Boolean, default=False)
    referral_code = Column(Text)
    referred_by = Column(Text)
    last_use = Column(DateTime(timezone=True))
    is_deleted = Column(Boolean, default=False)
    virtual_assistant_signature = Column(Text)
    header_signature = Column(Text)
    available_credits = Column(BigInteger, default=0)
    language = Column(Text)
    # Relaciones
    profiles = relationship("Profile", back_populates="establishment", cascade="all, delete-orphan")
    credits = relationship("EstablishmentCredit", back_populates="establishment", uselist=False, cascade="all, delete-orphan")
    reviews = relationship("EstablishmentReview", back_populates="establishment", cascade="all, delete-orphan")
    # Agregamos esta para el PIN de acceso
    access_pin = relationship("AppAccessPin", back_populates="establishment", uselist=False, cascade="all, delete-orphan")
    customers = relationship("Customer", back_populates="establishment", cascade="all, delete-orphan")



class Profile(Base):
    """Personal/Staff (Antes WTPerfiles)"""
    __tablename__ = "profiles"
    
    id = Column(BigInteger, primary_key=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    name = Column(Text)
    establishment_id = Column(String, ForeignKey("establishments.id", ondelete="CASCADE"), index=True)
    
    timezone = Column(Text)
    timezone_n = Column(Text)
    message_language = Column(Text, server_default="''")
    extra_data_1 = Column(Text)
    extra_data_2 = Column(Text)

    establishment = relationship("Establishment", back_populates="profiles")

class AppAccessPin(Base):
    """PIN de Seguridad (Antes WTModoAsistente)"""
    __tablename__ = "app_access_pins"
    
    id = Column(String, ForeignKey("establishments.id", ondelete="CASCADE"), primary_key=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now()) # Agregado por SQL
    pin = Column(BigInteger)
    
    establishment = relationship("Establishment", back_populates="access_pin")

class EstablishmentCredit(Base):
    """Saldo de WhatsApp (Antes Recordatorios)"""
    __tablename__ = "establishment_credits"
    
    id = Column(String, ForeignKey("establishments.id", ondelete="CASCADE"), primary_key=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now()) # Agregado por SQL
    available_credits = Column(BigInteger, default=0)
    
    establishment = relationship("Establishment", back_populates="credits")

class EstablishmentReview(Base):
    """Calificaciones sobre la aplicación"""
    __tablename__ = "establishment_reviews"
    
    id = Column(BigInteger, primary_key=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    establishment_id = Column(String, ForeignKey("establishments.id", ondelete="CASCADE"), index=True)
    
    rating = Column(Float)
    comment = Column(Text)
    customer_name = Column(Text) # Agregado por SQL

    establishment = relationship("Establishment", back_populates="reviews")


# Nueva Tabla: Para completar el bloque Core según tu SQL
class WhatsAppAuthPin(Base):
    """WhatsApp Technical Linking"""
    __tablename__ = "whatsapp_auth_pins"
    
    id = Column(String, primary_key=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    pin = Column(BigInteger)
    is_activated = Column(Boolean, default=False)
    send_attempts = Column(BigInteger, default=0)
    associated_phone = Column(BigInteger)
    
    # THIS WAS MISSING:
    # It must be defined as an ARRAY of BigIntegers to store the failed PINs

    validation_attempts = Column(ARRAY(BigInteger), default=[])
