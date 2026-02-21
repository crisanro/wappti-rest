from pydantic import BaseModel, Field, field_validator
from typing import Optional
from datetime import datetime, date

# --- 1. CITAS Y AGENDA (Appointments) ---

class AppointmentBase(BaseModel):
    appointment_date: datetime = Field(..., example="2026-02-10 15:30:00")
    timezone_region: str = Field(default="America/Guayaquil", example="America/Guayaquil")
    customer_id: int
    appointment_date: datetime
    reason: str = Field(..., example="Corte de cabello y barba")
    profile_id: Optional[int] = None # El especialista asignado

class AppointmentCreate(AppointmentBase):
    pass

class AppointmentUpdate(BaseModel):
    appointment_date: Optional[datetime] = None
    timezone_region: Optional[str] = None # Necesario si se cambia la fecha
    profile_id: Optional[int] = None
    reason: Optional[str] = None
    response_text: Optional[str] = None

    # Este validador detecta cualquier campo que venga como "" y lo convierte en None
    @field_validator('*', mode='before')
    @classmethod
    def transform_empty_string_to_none(cls, v):
        if v == "":
            return None
        return v
    

class AppointmentResponse(AppointmentBase):
    id: int
    created_at: datetime
    whatsapp_status: Optional[str] = None
    service_quality: Optional[str] = None # Feedback post-cita

    class Config:
        from_attributes = True


# --- 2. HISTORIAL Y VENTAS (Customer History) ---

class CustomerHistoryCreate(BaseModel):
    customer_id: int
    process_name: str = Field(..., example="Tinte completo")
    income: float = Field(default=0.0, ge=0)
    profile_id: Optional[int] = None
    notes: Optional[str] = None

class CustomerHistoryResponse(BaseModel):
    id: int
    profile_id: Optional[int]
    process_name: str
    income: float
    created_at: datetime # Lo enviaremos ya formateado como string
    notes: Optional[str] = ""
    class Config:
        from_attributes = True # Esto permite que Pydantic lea objetos de SQLAlchemy



# --- 4. AUDITORÍA DE USO (Usage Logs) ---

class UsageAuditResponse(BaseModel):
    created_at: datetime
    condition: str  # ej: "Gasto por Envío"
    value: int      # ej: -1 o 100
    observations: Optional[str] = None

    class Config:
        from_attributes = True


class UsageAuditLogCreate(BaseModel):
    """Esquema para registrar auditoría de uso de créditos"""
    condition: str = Field(..., example="Gasto por Envío")
    value: int = Field(..., example=-1)
    observations: Optional[str] = None

    class Config:
        from_attributes = True


class PlanItemCreate(BaseModel):
    description: str
    amount: float

class CustomerPlanCreate(BaseModel):
    customer_id: int
    title: str
    general_notes: Optional[str] = None
    items: list[PlanItemCreate] # Lista de rubros