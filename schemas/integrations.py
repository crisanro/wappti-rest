from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional

class WhatsAppAppointmentSchema(BaseModel):
    """
    Schema used for external integrations (Webhooks, n8n) 
    to send appointment details via WhatsApp.
    """
    phone: str = Field(..., description="Customer phone number (e.g., 593...)") # Antes numero
    customer_name: str = Field(..., description="Name of the customer") # Antes nombre
    location: str = Field(..., description="Appointment location or branch") # Antes lugar
    appointment_date: str = Field(..., description="Formatted date string") # Antes fecha
    contact_person: str = Field(..., description="Admin or staff contact name") # Antes contacto
    contact_phone: str = Field(..., description="Admin or staff contact phone") # Antes num_contacto


class ReviewOut(BaseModel):
    customer_name: str
    comment: str
    rating: float
    created_at: datetime

    class Config:
        from_attributes = True