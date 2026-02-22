from pydantic import BaseModel, Field
from typing import Optional

class CreateNotificationSchema(BaseModel):
    establishment_id: str = Field(..., min_length=5, description="ID del negocio")
    title: str = Field(..., min_length=1)
    description: str = Field(..., min_length=1)
    type: str = Field(..., example="system") # Obligatorio según tu requerimiento
    condition: Optional[str] = "info"
    redirection: Optional[str] = None

class CreateFeedbackRowSchema(BaseModel):
    appointment_id: str = Field(..., alias="id") # Mapeamos 'id' del JSON a appointment_id
    establishment_signature: str

class SubmitComplaintSchema(BaseModel):
    complaint: str = Field(..., min_length=1)
