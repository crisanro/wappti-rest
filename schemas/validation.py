from pydantic import BaseModel, Field
from typing import Optional

# Para verificar si un número ya existe en el sistema
class CheckPhoneSchema(BaseModel):
    phone: str = Field(..., description="Phone number to check (e.g., 593...)") # Antes cell

# Para solicitar el envío de un PIN de validación por WhatsApp
class PinRequestSchema(BaseModel):
    phone: int # Antes numero
    name: str # Antes nombre

# Para verificar el PIN ingresado por el usuario y completar el registro
class VerifyPinSchema(BaseModel):
    pin: int
    phone: int  # <--- RECIBIDO: El número de teléfono (BigInt)
    country: str
    referred_by: Optional[str] = None # Código de referido

class LinkReferralRequest(BaseModel):
    code_text: str
