from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from core.database import get_db
from core.auth import verificar_token_firebase
from schemas.integrations import WhatsAppAppointmentSchema
from services.whatsapp_service import WhatsAppService

# Apply security at the Router level: No one passes without a valid token
router = APIRouter(
    prefix="/whatsapp", 
    tags=["Integrations"],
    dependencies=[Depends(verificar_token_firebase)] 
)

# Initialize the WhatsApp Service
ws_service = WhatsAppService()

@router.post("/notify-appointment")
async def notify_new_appointment(data: WhatsAppAppointmentSchema, db: Session = Depends(get_db)):
    """
    This endpoint replaces the legacy n8n workflow:
    1. Sends a personalized appointment confirmation text.
    2. Sends a contact card.
    """
    
    # 1. Send Text Message using schema variables
    text_response = await ws_service.send_appointment_text(
        phone=data.phone,
        customer_name=data.customer_name,
        location=data.location,
        appointment_date=data.appointment_date
    )
    
    # Verify first step success
    if "error" in text_response:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, 
            detail={"error": "Text message dispatch failed", "meta": text_response}
        )

    # 2. Send Contact Card using contact variables
    contact_response = await ws_service.send_contact_card(
        phone=data.phone,
        contact_person=data.contact_person,
        contact_phone=data.contact_phone
    )

    if "error" in contact_response:
        return {
            "status": "partial_success", 
            "message": "Text message sent but contact card dispatch failed",
            "meta": contact_response
        }

    return {
        "status": "success", 
        "message": "Notification workflow completed successfully",
        "message_ids": [
            text_response.get("messages", [{}])[0].get("id"), 
            contact_response.get("messages", [{}])[0].get("id")
        ]
    }