from pydantic import BaseModel, Field, EmailStr
from typing import Optional, List, Dict, Any
from datetime import datetime

# --- 1. AYUDA Y CONTENIDO (FAQs, Tips, Tutorials) ---

class FAQResponse(BaseModel):
    id: int
    order_index: int
    question: str
    answer: str

    class Config:
        from_attributes = True

class GrowthTipResponse(BaseModel):
    id: int
    created_at: datetime
    title: str
    content: str
    category: Optional[str] = None

    class Config:
        from_attributes = True



# --- 2. FEEDBACK E INCIDENCIAS ---

class SystemAlertCreate(BaseModel):
    """Corresponde al modelo SystemAlert"""
    alert_type: str = Field(..., example="Technical Error")
    description: str
    email_contact: EmailStr

class UserSuggestionCreate(BaseModel):
    """Corresponde al modelo UserSuggestion"""
    suggestion_text: str

class ReviewCreate(BaseModel):
    """Corresponde al modelo EstablishmentReview"""
    rating: float = Field(..., ge=1, le=5)
    comment: Optional[str] = None


# --- 3. AUDITORÍA Y SEGURIDAD ---

class SystemAuditResponse(BaseModel):
    """Mapeo para el log de actividad detallada"""
    id: int
    created_at: datetime
    establishment_id: str
    action: str
    method: str
    path: str
    ip_address: Optional[str]
    payload: Optional[Dict[str, Any]]

    class Config:
        from_attributes = True

class AuthPinValidationBase(BaseModel):
    """Validación de PIN (Corresponde a AuthPinValidation)"""
    id: str
    pin_code: int = Field(..., ge=1000, le=999999)
    is_verified: bool = False