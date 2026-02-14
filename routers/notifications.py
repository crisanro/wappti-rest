from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import and_, text
from datetime import datetime, timedelta, timezone
import traceback
import pytz
from core.database import get_db
from core.auth import verify_firebase_token
from core.utils import register_action_log

# Import models with English names
from models import *

# Import updated schemas
# routers/communications.py
from schemas.communications import (
    CampaignCreate, # <--- Debe llamarse igual que en el archivo de schemas
    WhatsAppUpdateResponse,
    NotificationResponse,
    PrepareCampaignSchema
)

router = APIRouter(dependencies=[Depends(verify_firebase_token)])


@router.get("/")
def get_notifications(
    tz_name: str = "America/Guayaquil", 
    limit: int = 30, 
    db: Session = Depends(get_db), 
    token_data: dict = Depends(verify_firebase_token)
):
    try:
        establishment_id = token_data.get('uid')

        # 1. Zona Horaria
        try:
            user_tz = pytz.timezone(tz_name)
        except Exception:
            user_tz = pytz.UTC

        # 2. Consulta
        notifications_db = db.query(AppNotification).filter(
            AppNotification.establishment_id == establishment_id
        ).order_by(AppNotification.created_at.desc()).limit(limit).all()

        # 3. Transformación Dinámica Segura
        result = []
        for n in notifications_db:
            try:
                # Convertimos el objeto de la DB a un diccionario real
                row = {}
                for column in n.__table__.columns:
                    value = getattr(n, column.name)
                    
                    # Si el valor es una fecha, la procesamos con la zona horaria
                    if isinstance(value, datetime):
                        if value.tzinfo is None:
                            value = value.replace(tzinfo=pytz.UTC)
                        row[column.name] = value.astimezone(user_tz).isoformat()
                    else:
                        row[column.name] = value
                
                result.append(row)
            except Exception as inner_e:
                print(f"⚠️ Error procesando notificación: {inner_e}")
                continue

        return result

    except Exception as e:
        # Esto imprimirá en tu consola el error real (ej: si falta una tabla o columna fkey)
        print("--- DEBUG NOTIFICATIONS ERROR ---")
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@router.patch("/read-all")
def mark_all_as_read(
    db: Session = Depends(get_db), 
    token_data: dict = Depends(verify_firebase_token)
):
    establishment_id = token_data.get('uid')

    try:
        query_update = db.query(AppNotification).filter(
            and_(
                AppNotification.establishment_id == establishment_id, 
                AppNotification.is_read == False
            )
        )

        updated_rows = query_update.update(
            {"is_read": True}, 
            synchronize_session=False
        )
        
        db.commit()

        return {
            "status": "success", 
            "message": f"Marked {updated_rows} notifications as read."
        }

    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/read/{id}")
def mark_one_as_read(
    id: int, 
    db: Session = Depends(get_db), 
    token_data: dict = Depends(verify_firebase_token)
):
    establishment_id = token_data.get('uid')

    notification = db.query(AppNotification).filter(
        and_(
            AppNotification.id == id, 
            AppNotification.establishment_id == establishment_id
        )
    ).first()

    if not notification:
         raise HTTPException(status_code=404, detail="Notification not found")

    notification.is_read = True
    
    try:
        db.commit()
        return {"status": "success", "id": id}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

