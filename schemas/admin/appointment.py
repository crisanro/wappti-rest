from pydantic import BaseModel
from typing import Optional
from sqlalchemy import text

# Esquema para recibir la data de n8n
class AppointmentConfirmation(BaseModel):
    appointment_id: int
    whatsapp_id: str  # El ID que devuelve la API de WhatsApp

class SingleUpdatePayload(BaseModel):
    appointment_id: int
    whatsapp_id: str
    establishment_id: str


class WhatsAppStatusPayload(BaseModel):
    whatsapp_id: str
    status: Optional[str] = None
    response_text: Optional[str] = None
    error_code: Optional[str] = None
    error_title: Optional[str] = None
