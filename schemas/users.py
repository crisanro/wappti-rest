from pydantic import BaseModel, EmailStr, Field, computed_field
from typing import Optional, List
from datetime import datetime, timezone

# --- 1. ETIQUETAS (Customer Tags) ---

# --- TAG SCHEMAS ---
class TagBase(BaseModel):
    name: str = Field(..., example="Cliente VIP")

class TagCreate(TagBase):
    pass

class TagResponse(TagBase):
    id: int
    total_customers: int 

    class Config:
        from_attributes = True

class TagUpdateSchema(BaseModel):
    tag_id: int
    action: int  # 1 añadir, 0 quitar

# --- CUSTOMER SCHEMAS ---

# 1. La base mínima (Lo que comparten Listas, Detalles y Creación)
class CustomerCore(BaseModel):
    first_name: str
    last_name: str
    phone: int = Field(..., description="Número de celular sin el +")
    country_code: int = Field(..., example=593)

    class Config:
        from_attributes = True

# 2. Schema para CREACIÓN (Añadimos lo que falta para el POST)
class CustomerCreate(CustomerCore):
    email: Optional[str] = None
    identification_id: Optional[str] = None
    profile_id: int
    country_name: str
    language: str = "es"  # Defaulting to "es" if not provided

class CustomerUpdate(BaseModel):
    # Campos que antes eran obligatorios, ahora son opcionales con valor None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    phone: Optional[int] = None
    email: Optional[str] = None
    identification_id: Optional[str] = None
    notes: Optional[str] = None
    country_code: Optional[int] = None # Cambiado a Optional
    country_name: Optional[str] = None # Cambiado a Optional
    language: Optional[str] = None     # Cambiado a Optional
    tag_ids: Optional[List[int]] = None

    class Config:
        from_attributes = True

# 3. Schema para LISTAS (Optimizado para el túnel y velocidad)
class CustomerListResponse(CustomerCore):
    id: int
    last_visit_date: Optional[datetime] = None 

    @computed_field
    def last_visit_unix(self) -> Optional[int]:
        if not self.last_visit_date: return None
        dt = self.last_visit_date
        return int(dt.replace(tzinfo=timezone.utc).timestamp()) if dt.tzinfo is None else int(dt.timestamp())

    @computed_field
    def hours_since_last_visit(self) -> Optional[float]:
        if not self.last_visit_date: return None
        dt = self.last_visit_date
        if dt.tzinfo is None: dt = dt.replace(tzinfo=timezone.utc)
        diff = datetime.now(timezone.utc) - dt
        return round(diff.total_seconds() / 3600, 2)

# 4. Schema para DETALLE (Hereda de Core + campos extra)
class CustomerDetailResponse(CustomerCore):
    id: int
    establishment_id: str
    email: Optional[EmailStr] = None
    identification_id: Optional[str] = None
    country_name: str
    notes: Optional[str] = None
    is_active: bool
    created_at: datetime
    last_visit: Optional[datetime] = None
    tag_ids: List[int] = []