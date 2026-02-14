from sqlalchemy import Column, String, Text, DateTime, BigInteger, ForeignKey, func
from sqlalchemy.orm import relationship
from core.database import Base

class Appointment(Base):
    """Citas y Agenda (Antes WTRecordatorios)"""
    __tablename__ = "appointments"
    
    id = Column(BigInteger, primary_key=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # FKeys con integridad referencial
    establishment_id = Column(String, ForeignKey("establishments.id", ondelete="CASCADE"), index=True)
    customer_id = Column(BigInteger, ForeignKey("customers.id", ondelete="CASCADE"), index=True)
    # Nota: El SQL no muestra una FK explícita para profile_id, pero mantenerla como lógica es mejor
    profile_id = Column(BigInteger, nullable=True) 
    
    appointment_date = Column(DateTime(timezone=True))
    reason = Column(Text)
    response_text = Column(Text)
    service_quality = Column(Text)
    complaint = Column(Text)
    
    whatsapp_id = Column(Text)
    whatsapp_id_2 = Column(Text) 
    whatsapp_status = Column(Text)

    # Relaciones
    customer = relationship("Customer", back_populates="appointments")
    # Si quieres poder acceder al local desde la cita:
    # establishment = relationship("Establishment")

class CalendarNote(Base):
    """
    Notas internas en el calendario.
    """
    __tablename__ = "calendar_notes"
    
    id = Column(BigInteger, primary_key=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    establishment_id = Column(String, ForeignKey("establishments.id", ondelete="CASCADE"), index=True)
    profile_id = Column(BigInteger, nullable=True)
    
    title = Column(Text)
    description = Column(Text)
    # OJO: El SQL dice 'without time zone' para event_date
    event_date = Column(DateTime(timezone=False)) 
    emoji_id = Column(BigInteger)