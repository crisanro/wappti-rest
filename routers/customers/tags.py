from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import and_, or_, any_, asc, func
from datetime import datetime, timezone, timedelta
from typing import Optional, List
import traceback
import pytz
from core.database import get_db
from core.auth import verify_firebase_token # Asegúrate que el nombre coincida con core/auth.py
from core.utils import register_action_log

# Importación de Modelos (Ubicaciones correctas)
from models import *

# Importación de Schemas (Usando los nombres de tu archivo schemas/users.py)
from schemas.users import CustomerCreate, CustomerUpdate,TagUpdateSchema, TagBase, TagResponse, CustomerListResponse, CustomerListResponse
from schemas.operations import CustomerPlanCreate
from schemas.financials import DebtCreate, PaymentCreate
router = APIRouter(dependencies=[Depends(verify_firebase_token)])

# --- 6. TAG MANAGEMENT (TOGGLE) ---
@router.get("/{customer_id}/tags", response_model=List[TagResponse])
def get_customer_tags(
    customer_id: int,
    db: Session = Depends(get_db),
    token_data: dict = Depends(verify_firebase_token)
):
    try:
        establishment_id = token_data.get('uid')

        # 1. Obtener al cliente y su columna tag_ids (ARRAY)
        customer = db.query(Customer).filter(
            Customer.id == customer_id,
            Customer.establishment_id == establishment_id
        ).first()

        if not customer:
            raise HTTPException(status_code=404, detail="customer_not_found")

        # Si no tiene tags, devolvemos lista vacía
        if not customer.tag_ids:
            return []

        # 2. Consultar los nombres en la tabla customer_tags
        # Usamos el nombre del MODELO: CustomerTag
        tags = db.query(CustomerTag).filter(
            CustomerTag.id.in_(customer.tag_ids),
            CustomerTag.establishment_id == establishment_id
        ).all()

        return tags

    except Exception as e:
        print(f"🚨 Error: {str(e)}")
        raise HTTPException(
            status_code=500, 
            detail="customer_tags_fetch_error"
        )
    

@router.patch("/{customer_id}", response_model=CustomerListResponse)
def update_customer_info(
    customer_id: int,
    data: CustomerUpdate,
    request: Request, # <--- Agregado para el log de IP
    db: Session = Depends(get_db),
    token_data: dict = Depends(verify_firebase_token)
):
    """
    Updates customer fields including country information.
    """
    uid = token_data.get('uid')
    
    # 1. Buscar al cliente
    customer = db.query(Customer).filter(
        Customer.id == customer_id, 
        Customer.establishment_id == uid
    ).first()

    if not customer:
        raise HTTPException(status_code=404, detail="customer_not_found")

    # 2. Extraer solo los campos que vienen en el JSON (incluyendo country_code/name)
    # Ignoramos tag_ids aquí porque eso se maneja en otro endpoint específico (/tags)
    update_data = data.model_dump(exclude_unset=True, exclude={"tag_ids"})

    # 3. Aplicar los cambios dinámicamente
    for key, value in update_data.items():
        setattr(customer, key, value)

    try:
        # 4. Registrar Auditoría (Antes del commit para asegurar atomicidad)
        register_action_log(
            db=db, 
            establishment_id=uid, 
            action="UPDATE_CUSTOMER_INFO", 
            method="PATCH", 
            path=request.url.path, 
            payload=update_data, # Solo guardamos lo que realmente cambió
            request=request
        )

        db.commit()
        db.refresh(customer)
        
        return customer

    except Exception as e:
        db.rollback()
        print(f"🚨 UPDATE ERROR: {str(e)}")
        raise HTTPException(status_code=500, detail="internal_update_error")
    

@router.patch("/{customer_id}/tags")
def toggle_customer_tag(
    customer_id: int, 
    data: TagUpdateSchema, 
    request: Request, # Asegúrate de que esté aquí
    db: Session = Depends(get_db), 
    token_data: dict = Depends(verify_firebase_token)
):
    establishment_id = token_data.get('uid')
    
    customer = db.query(Customer).filter(and_(Customer.id == customer_id, Customer.establishment_id == establishment_id)).first()
    tag = db.query(CustomerTag).filter(and_(CustomerTag.id == data.tag_id, CustomerTag.establishment_id == establishment_id)).first()

    if not customer or not tag:
        raise HTTPException(status_code=404, detail="Customer or Tag not found")

    current_tags = list(customer.tag_ids) if customer.tag_ids else []
    changed = False
    action_type = "ADD" if data.action == 1 else "REMOVE"

    if data.action == 1: # ADD
        if data.tag_id not in current_tags:
            current_tags.append(data.tag_id)
            tag.total_customers = (tag.total_customers or 0) + 1
            changed = True
    elif data.action == 0: # REMOVE
        if data.tag_id in current_tags:
            current_tags.remove(data.tag_id)
            tag.total_customers = max(0, (tag.total_customers or 1) - 1)
            changed = True
    
    if changed:
        customer.tag_ids = current_tags
        
        # Log más descriptivo
        register_action_log(
            db, 
            establishment_id=establishment_id, 
            action="TAG_TOGGLE", 
            method="PATCH", 
            path=request.url.path, 
            payload={
                "customer_id": customer_id, 
                "tag_id": data.tag_id, 
                "tag_name": tag.name, 
                "action": action_type
            }, 
            request=request
        )
        
        db.commit()
    
    return {"status": "success", "updated_tags": customer.tag_ids}