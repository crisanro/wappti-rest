from pydantic import BaseModel, Field

class CreditReload(BaseModel):
    amount: int = Field(..., gt=0, description="Cantidad de créditos a sumar al establecimiento")

    class Config:
        extra = "forbid"
