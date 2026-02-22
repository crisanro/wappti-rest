from sqlalchemy import Column, String, Text, Boolean, DateTime, BigInteger, Integer, ForeignKey, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from core.database import Base

class WhatsAppCampaign(Base):
    """Gestión de campañas de marketing enviadas por el local."""
    __tablename__ = "whatsapp_campaigns"
    
    id = Column(BigInteger, primary_key=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    establishment_id = Column(String, ForeignKey("establishments.id", ondelete="CASCADE"), index=True)
    
    name = Column(Text)
    description = Column(Text)
    status = Column(Text, default="draft")
    link = Column(Text)
    message_content = Column(Text)
    responses = Column(JSONB, default={}) 
    
    dispatches = relationship("WhatsAppDispatch", back_populates="campaign", cascade="all, delete-orphan")

class WhatsAppDispatch(Base):
    """Registro individual de cada mensaje enviado (Crítico para auditoría de abuso)"""
    __tablename__ = "whatsapp_dispatches"
    
    id = Column(BigInteger, primary_key=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    campaign_id = Column(BigInteger, ForeignKey("whatsapp_campaigns.id", ondelete="SET NULL"), nullable=True)
    establishment_id = Column(String, ForeignKey("establishments.id", ondelete="CASCADE"), index=True)
    customer_id = Column(BigInteger, nullable=True) # Sin FK estricta para permitir anonimización
    
    # Columnas según tu SQL (Clave para reportes)
    phone_number = Column(BigInteger)
    whatsapp_id = Column(Text)      # wamid
    status = Column(Text)           # sent, delivered, etc.
    status_id_ws = Column(Text)     # ID interno de estado del proveedor
    country = Column(Text)          # Guardado a fuego para estadística
    customer_name = Column(Text)    # Guardado a fuego (útil si borran al cliente)
    should_send = Column(Boolean, default=True)

    campaign = relationship("WhatsAppCampaign", back_populates="dispatches")

class WhatsAppSession(Base):
    """Estado de la conexión de WhatsApp (Mapeada según SQL)"""
    __tablename__ = "whatsapp_sessions"
    
    id = Column(BigInteger, primary_key=True) # Tu SQL dice BigInt, no String
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    whatsapp_session_id = Column(Text)
    status = Column(Text)

class WhatsAppError(Base):
    """Logs técnicos de fallos vinculados a citas"""
    __tablename__ = "whatsapp_errors"
    
    id = Column(BigInteger, primary_key=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    # Según tu SQL, se vincula a citas, no a establecimientos directamente
    appointment_id = Column(BigInteger, nullable=True) 
    error_message = Column(Text)

class AppNotification(Base):
    """Notificaciones internas de la App (Ajustado nombre según SQL)"""
    __tablename__ = "app_notifications"
    
    id = Column(BigInteger, primary_key=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    establishment_id = Column(String, index=True) # En el SQL no tiene FK explícita
    
    title = Column(Text)
    description = Column(Text)
    condition = Column(Text)      # ej: 'credit_low'
    type = Column(Text)            # ej: 'system', 'marketing'
    redirection = Column(Text) # Nombre según SQL
    is_read = Column(Boolean, default=False)

class AppAd(Base):
    """Publicidad Global del Sistema"""
    __tablename__ = "app_ads"
    
    id = Column(BigInteger, primary_key=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    title = Column(Text)
    description = Column(Text)
    image_url = Column(Text)
    cta_url = Column(Text)
    internal_name = Column(Text)
    views_count = Column(BigInteger, default=0)

    hex_color = Column(Text)
