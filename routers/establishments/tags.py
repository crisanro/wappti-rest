from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_, func
from datetime import datetime, timezone, timedelta
import pytz
from typing import Optional, List
import traceback

from core.database import get_db
from core.auth import verify_firebase_token
from core.utils import register_action_log

# Import English models
from models import *

# Import updated schemas
from schemas.operations import (
    CustomerHistoryCreate, 
    AppointmentCreate, 
    UsageAuditLogCreate
)
from schemas.users import TagResponse, TagCreate

router = APIRouter(dependencies=[Depends(verify_firebase_token)])


# --- SECTION: TAGS (Labels with duplicate validation) ---
@router.get("/", response_model=List[TagResponse])
def get_all_establishment_tags(
    db: Session = Depends(get_db),
    token_data: dict = Depends(verify_firebase_token)
):
    try:
        establishment_id = token_data.get('uid')

        # Consultamos la tabla maestra 'customer_tags' usando el modelo 'CustomerTag'
        tags = db.query(CustomerTag).filter(
            CustomerTag.establishment_id == establishment_id
        ).order_by(CustomerTag.name.asc()).all()

        return tags

    except Exception as e:
        print(f"🚨 Error fetching all tags: {str(e)}")
        raise HTTPException(
            status_code=500, 
            detail="establishment_tags_fetch_error"
        )


@router.post("/", status_code=status.HTTP_201_CREATED)
def create_tag(
    data: TagCreate, # <--- Ahora recibimos un objeto JSON
    request: Request,
    db: Session = Depends(get_db), 
    token_data: dict = Depends(verify_firebase_token)
):
    """
    Creates a new customer tag receiving data via Body JSON.
    """
    establishment_id = token_data.get('uid')
    
    # 1. Limpieza y validación
    clean_name = data.name.strip()
    if not clean_name:
        raise HTTPException(status_code=400, detail="tag_name_cannot_be_empty")

    # 2. Validar duplicados (minúsculas vs minúsculas)
    exists = db.query(CustomerTag).filter(
        and_(
            CustomerTag.establishment_id == establishment_id,
            func.lower(CustomerTag.name) == clean_name.lower()
        )
    ).first()

    if exists:
        raise HTTPException(
            status_code=400, 
            detail="tag_already_exists"
        )
    
    # 3. Crear el objeto
    new_tag = CustomerTag(
        name=clean_name, 
        establishment_id=establishment_id, 
        total_customers=0,
        created_at=datetime.now(timezone.utc) 
    )
    
    try:
        db.add(new_tag)
        
        # 4. Registrar Log ANTES del commit para mayor seguridad
        register_action_log(
            db=db, 
            establishment_id=establishment_id, 
            action="CREATE_TAG", 
            method="POST",
            path=request.url.path, 
            payload={"name": clean_name}, 
            request=request
        )

        db.commit()
        db.refresh(new_tag)
        
        return {}

    except Exception as e:
        db.rollback()
        print(f"🚨 CREATE TAG ERROR: {str(e)}")
        raise HTTPException(status_code=500, detail="internal_create_tag_error")


@router.delete("/{tag_id}")
def delete_tag(
    tag_id: int, 
    request: Request,
    db: Session = Depends(get_db), 
    token_data: dict = Depends(verify_firebase_token)
):
    establishment_id = token_data.get('uid')
    
    # 1. Buscar el tag
    tag = db.query(CustomerTag).filter(
        and_(CustomerTag.id == tag_id, CustomerTag.establishment_id == establishment_id)
    ).first()

    if not tag:
        raise HTTPException(status_code=404, detail="tag_not_found")

    # 2. Validación de integridad
    if tag.total_customers > 0:
        raise HTTPException(
            status_code=400, 
            detail=f"cannot_delete_tag_in_use_{tag.total_customers}"
        )

    # 3. Guardar datos para el log antes de borrar
    tag_name = tag.name 

    try:
        # 4. Registrar el Log (Antes del commit)
        register_action_log(
            db=db, 
            establishment_id=establishment_id, 
            action="DELETE_TAG", 
            method="DELETE",
            path=request.url.path, 
            payload={"id": tag_id, "name": tag_name}, 
            request=request
        )

        # 5. Borrar de la DB
        db.delete(tag)
        db.commit()
        
        return {"status": "success", "message": "tag_deleted"}

    except Exception as e:
        db.rollback()
        print(f"🚨 DELETE TAG ERROR: {str(e)}")
        raise HTTPException(status_code=500, detail="internal_delete_error")
