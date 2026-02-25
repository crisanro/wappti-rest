from pydantic import BaseModel, Field
from typing import Optional



class CreditReload(BaseModel):
    amount: int = Field(..., gt=0, description="Cantidad de créditos a sumar al establecimiento")

    class Config:
        extra = "forbid"


class GlobalPaymentProcessor(BaseModel):
    establishment_id: str
    amount: float = Field(..., gt=0)
    reference_id: str
    # Commission rates as decimals
    rate_first_pay: float = 0.60 
    rate_second_pay: float = 0.30
    rate_third_pay: float = 0.15
    invoice_link: Optional[str] = None
