from pydantic import BaseModel, EmailStr, Field
from typing import Optional
from datetime import datetime, date

# --- 1. ESTABLECIMIENTO (The Business) ---

class EstablishmentInfo(BaseModel):
    """Estructura completa para mostrar o registrar un local"""
#   id: str = Field(..., description="Firebase UID")
    name: str 
    email: EmailStr 
    country: str 
    whatsapp: str
    contact_card: Optional[str] = None
    header_signature: Optional[str] = None
    message_signature: Optional[str] = None
    virtual_assistant_signature: Optional[str] = None
    referral_code: Optional[str] = None
#    is_suspended: bool = False

    class Config:
        from_attributes = True

class EstablishmentUpdate(BaseModel):
    name: Optional[str] = Field(None, description="Nombre comercial del local")
    message_signature: Optional[str] = Field(None, description="Firma al final de los mensajes")
    contact_card: Optional[str] = None
    virtual_assistant_signature: Optional[str] = None
    header_signature: Optional[str] = None

    class Config:
        # Bloquea cualquier intento de enviar campos que no estén arriba
        extra = "forbid"


# Schema para recibir los datos (el id y email vienen del token)
class SetupEstablishmentRequest(BaseModel):
    name: str
    timezone: str # Aquí recibimos "America/Guayaquil"

# --- 2. SEGURIDAD (Security PIN) ---

class PinUpdate(BaseModel):
    """
    Validación estricta de 6 dígitos. 
    Usamos String para el pattern para asegurar que '000123' no se convierta en '123'.
    """
    pin: str = Field(
        ..., 
        min_length=6, 
        max_length=6, 
        pattern=r"^\d{6}$", 
        description="El PIN debe ser exactamente de 6 dígitos numéricos"
    )


# --- 3. PERFILES / STAFF (The Team) ---

# 1. Base: Lo que es común a todos
class ProfileBase(BaseModel):
    name: str = Field(..., example="Juan Pérez")
    timezone: str = Field(..., example="America/Guayaquil")
    message_language: Optional[str] = ""
    extra_data_1: Optional[str] = None
    extra_data_2: Optional[str] = None

# 2. Create: Lo que se envía para crear uno nuevo
class ProfileCreate(ProfileBase):
    # El establishment_id suele venir del token, 
    # pero si lo necesitas en el body, lo dejamos opcional
    pass

# 3. Update: Todo opcional para actualizaciones parciales (PATCH)
class ProfileUpdate(BaseModel):
    name: Optional[str] = None
    timezone: Optional[str] = None
    message_language: Optional[str] = None
    extra_data_1: Optional[str] = None
    extra_data_2: Optional[str] = None

# 4. Response: Lo que el cliente ve (Sin created_at ni establishment_id)
class ProfileResponse(BaseModel):
    id: int
    name: str
    timezone: str
    message_language: Optional[str] = ""
    extra_data_1: Optional[str] = None
    extra_data_2: Optional[str] = None

    class Config:
        from_attributes = True # Permite leer objetos de SQLAlchemy

class TutorialLinkResponse(BaseModel):
    id: int
    name: str
    link: str

    class Config:
        from_attributes = True


# --- 3. NOTAS DE CALENDARIO (Internal Notes) ---
class CalendarNoteBase(BaseModel):
    title: str
    description: Optional[str] = None
    event_date: date  # El frontend manda YYYY-MM-DD
    emoji_id: Optional[int] = None
    profile_id: Optional[int] = None

class CalendarNoteCreate(CalendarNoteBase):
    pass

class CalendarNoteResponse(BaseModel):
    id: int
    title: str
    description: Optional[str]
    event_date: date # Lo devolvemos como fecha limpia
    emoji_id: Optional[int]
    profile_id: Optional[int]

    class Config:
        from_attributes = True