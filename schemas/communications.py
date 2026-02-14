from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime

# --- 1. MARKETING Y CAMPAÑAS ---

class CampaignCreate(BaseModel):
    name: str = Field(..., min_length=3, max_length=100, example="Promoción de Verano")
    description: Optional[str] = Field(None, example="Campaña dirigida a clientes VIP con 20% de descuento")

    class Config:
        extra = "forbid" # No permitimos campos extra

"""# En schemas/communications.py
class CampaignCreateResponse(BaseModel): # Renombra la clase aquí
    name: str
    description: Optional[str] = None
"""

class WhatsAppCampaignResponse(BaseModel):
    """Respuesta detallada de una campaña"""
    id: int
    establishment_id: str
    name: Optional[str]
    template_name: Optional[str]
    status: Optional[str]
    total_messages: int
    created_at: datetime

    class Config:
        from_attributes = True


# --- 2. ENVÍOS (DISPATCHES) ---

class WhatsAppDispatchResponse(BaseModel):
    """Estado de envío individual de un mensaje"""
    id: int
    campaign_id: Optional[int]
    customer_id: int
    whatsapp_id: Optional[str]
    status: Optional[str]
    error_message: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True


# --- 3. NOTIFICACIONES INTERNAS ---

class NotificationResponse(BaseModel):
    """Esquema sincronizado con el router para listar notificaciones"""
    id: int
    title: str
    message: str
    is_read: bool
    action_url: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True


# --- 4. PUBLICIDAD (ADS) ---

class AppAdResponse(BaseModel):
    """Banners publicitarios"""
    id: int
    title: str
    image_url: str
    target_url: str
    position: int

    class Config:
        from_attributes = True


# --- 5. COMPATIBILIDAD CON LÓGICA EXISTENTE ---

class WhatsAppUpdateResponse(BaseModel):
    """
    IMPORTANTE: Este nombre es el que busca tu router en la línea 19.
    Se usa para actualizar el JSON de respuestas de Meta.
    """
    responses: Dict[str, Any]

class PrepareCampaignSchema(BaseModel):
    """Lógica para preparar el envío masivo por etiquetas"""
    campaign_id: int
    tag_id: int

    