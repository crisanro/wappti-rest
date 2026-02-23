from fastapi import APIRouter, Depends, HTTPException, status
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
    PrepareCampaignSchema, FollowupRequest
)

router = APIRouter(dependencies=[Depends(verify_firebase_token)])

@router.get("/")
def get_notifications(
    tz_name: str = "America/Guayaquil", 
    limit: int = 30, 
    db: Session = Depends(get_db), 
    token_data: dict = Depends(verify_firebase_token)
):
    """
    Retrieves internal app notifications for the authenticated establishment.
    Converts 'created_at' to the local time based on the provided timezone.
    """
    establishment_id = token_data.get('uid')

    try:
        # 1. Setup Local Timezone
        try:
            local_tz = pytz.timezone(tz_name)
        except Exception:
            # Fallback to UTC if timezone is invalid
            local_tz = pytz.UTC

        # 2. Database Query
        notifications_db = db.query(AppNotification).filter(
            AppNotification.establishment_id == establishment_id
        ).order_by(AppNotification.created_at.desc()).limit(limit).all()

        # 3. Explicit Transformation
        result = []
        for n in notifications_db:
            # Safe Date Handling
            local_created_at = None
            if n.created_at:
                # If DB date is naive (no timezone), we assume it's UTC
                dt = n.created_at
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=pytz.UTC)
                
                # CONVERSION TO LOCAL TIME (America/Guayaquil)
                local_created_at = dt.astimezone(local_tz).isoformat()

            result.append({
                "id": n.id,
                "title": n.title or "",
                "description": n.description or "",
                "condition": n.condition or "",
                "redirection": n.redirection or "",
                "is_read": n.is_read,
                "created_at": local_created_at,  # Now in local time
                "type: n.type
            })

        return result

    except Exception as e:
        print(f"🚨 NOTIFICATIONS LOCAL TIME ERROR: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
            detail="Internal server error while processing local time for notifications."
        )


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




@router.post("/register-followup", status_code=status.HTTP_201_CREATED)
async def register_followup(
    data: FollowupRequest,
    db: Session = Depends(get_db),
    token_data: dict = Depends(verify_firebase_token)
):
    # Obtenemos el ID del establecimiento desde el JWT
    user_id = str(token_data.get('uid'))
    
    try:
        # Creamos el registro en la tabla pending_followups
        new_followup = PendingFollowup(
            establishment_id=user_id,
            followup_type=data.followup_type.value # Guardamos el string (ej: "abandoned_checkout")
        )
        
        db.add(new_followup)
        db.commit()
        
        return {
            "status": "success",
            "message": "Followup registered successfully",
            "type": data.followup_type
        }
        
    except Exception as e:
        db.rollback()
        # Aquí también podrías usar tu webhook de seguridad si consideras que fallar aquí es crítico
        print(f"🚨 Error registering followup: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
            detail="COULD_NOT_REGISTER_FOLLOWUP"

        )
