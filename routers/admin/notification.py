import os
from fastapi import APIRouter, Depends, HTTPException, Header, status
from sqlalchemy.orm import Session
from core.database import get_db
from models import AppNotification, Establishment
from schemas.admin.notification import  CreateNotificationSchema
from core.auth import verify_superadmin_key  # Tu función que valida el header X-Superadmin-Key

router = APIRouter(
    prefix="/admin",
    tags=["Admin Appointments"],
    dependencies=[Depends(verify_superadmin_key)] 
)


@router.post("/send-notification")
async def send_app_notification(
    data: CreateNotificationSchema,
    db: Session = Depends(get_db),
):
    try:
        # 1. Verificar que el establecimiento EXISTE de verdad
        # Esto previene el error: Key (establishment_id)=() is not present
        business = db.query(Establishment).filter(Establishment.id == data.establishment_id).first()
        if not business:
            raise HTTPException(
                status_code=404, 
                detail=f"Establishment ID '{data.establishment_id}' not found in database"
            )

        # 2. Crear objeto (Solo con los datos que recibimos)
        # id, created_at y is_read se llenan por DEFAULT en la DB/Modelo
        new_notif = AppNotification(
            establishment_id=data.establishment_id,
            title=data.title,
            description=data.description,
            type=data.type,
            condition=data.condition,
            redirection=data.redirection
        )

        db.add(new_notif)
        db.commit()
        db.refresh(new_notif)

        return {
            "status": "success",
            "message": f"Notification created for {business.name}",
            "notification_id": new_notif.id
        }

    except HTTPException as he:
        raise he
    except Exception as e:
        db.rollback()
        print(f"🚨 ERROR: {str(e)}")
        raise HTTPException(status_code=500, detail="INTERNAL_SERVER_ERROR")
