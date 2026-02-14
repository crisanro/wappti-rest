from sqlalchemy import Column, String, Text, Boolean, DateTime, BigInteger, Integer, Float, func, ForeignKey
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import JSONB
from core.database import Base

class SystemAudit(Base):
    """Registro de actividad técnica (Caja negra de la API)."""
    __tablename__ = "system_audit"
    
    # CAMBIADO A BIGINTEGER: Soporta billones de registros de logs
    id = Column(BigInteger, primary_key=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    establishment_id = Column(String, index=True)
    
    action = Column(String)           
    method = Column(String)           
    path = Column(Text)               
    payload = Column(JSONB)           
    ip = Column(String)               
    status_code = Column(Integer)     

class UsageAuditLog(Base):
    """Registro de movimientos de créditos (Historial de transacciones)."""
    __tablename__ = "usage_audit_logs"
    
    id = Column(BigInteger, primary_key=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    establishment_id = Column(String, index=True)
    
    condition = Column(Text)          
    value = Column(BigInteger)        
    observations = Column(Text)

class SystemAlert(Base):
    """Reportes de errores técnicos o de pago vinculados a un local."""
    __tablename__ = "system_alerts"
    
    id = Column(BigInteger, primary_key=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # CAMBIO: Ahora es formalmente una FK hacia establishments
    establishment_id = Column(String, ForeignKey("establishments.id", ondelete="CASCADE"), index=True)
    
    type = Column(Text)               
    payment_id = Column(Text)         
    email = Column(Text)              
    is_verified = Column(Boolean, default=False)

    # Relación para poder acceder a: alerta.establishment.name
    establishment = relationship("Establishment")

class UserSuggestion(Base):
    """Buzón de sugerencias de los establecimientos."""
    __tablename__ = "user_suggestions"
    
    id = Column(BigInteger, primary_key=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    establishment_id = Column(String)
    
    suggestion = Column(Text)         
    response = Column(Text)           

class FAQ(Base):
    """Preguntas frecuentes para soporte."""
    __tablename__ = "faqs"
    
    id = Column(BigInteger, primary_key=True) 
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    question = Column(Text)
    answer = Column(Text)
    display_order = Column(Float)     

class TutorialLink(Base):
    """Biblioteca de videos tutoriales."""
    __tablename__ = "tutorial_links"
    
    id = Column(BigInteger, primary_key=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    name = Column(Text)
    link = Column(Text)
    establishment_id = Column(String)

class PendingFollowup(Base):
    """
    Programación de seguimientos futuros.
    """
    __tablename__ = "pending_followups"
    
    id = Column(BigInteger, primary_key=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    establishment_id = Column(String, ForeignKey("establishments.id", ondelete="CASCADE"), index=True)
    followup_type = Column(Text) # ej: "Reactivación"

class GrowthTip(Base):
    """Consejos de marketing."""
    __tablename__ = "growth_tips"
    
    id = Column(BigInteger, primary_key=True) # SQL dice bigint
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    platform = Column(Text)  # Antes: category
    title = Column(Text)
    message = Column(Text)   # Antes: content
    link = Column(Text)      # Nueva según SQL



class SystemBlockedIP(Base):
    """
    Stores blacklisted IP addresses to mitigate DDoS attacks.
    """
    __tablename__ = "system_blocked_ips"

    id = Column(BigInteger, primary_key=True, index=True)
    ip_address = Column(String, unique=True, index=True, nullable=False)
    reason = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    is_active = Column(Boolean, default=True)

    def __repr__(self):
        return f"<SystemBlockedIP(ip='{self.ip_address}', active={self.is_active})>"