from pydantic import BaseModel

class BillingProfileCreate(BaseModel):
    customer_id: int
    tax_id_type: str
    tax_id_number: str
    business_name: str