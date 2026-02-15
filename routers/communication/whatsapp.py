from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import and_, text
from datetime import datetime, timedelta, timezone
import traceback

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
def get_campaign_list(
    db: Session = Depends(get_db), 
    token_data: dict = Depends(verify_firebase_token)
):
    establishment_id = token_data.get('uid')

    campaigns = db.query(WhatsAppCampaign).filter(
        WhatsAppCampaign.establishment_id == establishment_id
    ).order_by(WhatsAppCampaign.created_at.desc()).all()

    # Retornamos solo lo esencial para la lista
    return [
        {
            "id": c.id,
            "name": c.name,
            "status": c.status,
            "description": c.description,
            "created_at": c.created_at.isoformat() if c.created_at else None,
            # No enviamos 'responses' aquí para ahorrar megas
        } for c in campaigns
    ]


@router.patch("/{id}")
def update_whatsapp_config(id: int, data: WhatsAppUpdateResponse, db: Session = Depends(get_db)):
    campaign = db.query(WhatsAppCampaign).filter(WhatsAppCampaign.id == id).first()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    
    campaign.responses = data.responses
    db.commit()
    return {"status": "JSON updated", "id": id}


@router.post("/", status_code=201)
def create_marketing_campaign(
    data: CampaignCreate, 
    db: Session = Depends(get_db), 
    token_data: dict = Depends(verify_firebase_token)
):
    """
    Initializes a new WhatsApp marketing campaign as a draft.
    """
    try:
        # Extract establishment ID from token
        uid = token_data.get('uid')
        
        # Initialize the campaign with system-controlled values
        new_campaign = WhatsAppCampaign(
            establishment_id=uid,
            name=data.name.strip(),
            description=data.description,
            status="draft",
            responses={},  # Empty JSONB structure
            created_at=datetime.now(timezone.utc)
        )

        db.add(new_campaign)
        db.commit()
        db.refresh(new_campaign)
        
        # Audit log
        register_action_log(
            db, 
            uid, 
            "CREATE_CAMPAIGN", 
            "POST", 
            "/whatsapp/new-campaign", 
            data.model_dump()
        )

        return {
            "status": "success", 
            "message": "Campaign successfully created as draft",
            "campaign_id": new_campaign.id
        }

    except Exception as e:
        db.rollback()
        # Internal log for debugging
        print(f"🚨 DATABASE ERROR: {str(e)}")
        raise HTTPException(
            status_code=500, 
            detail="Internal server error while saving the campaign"
        )
    

@router.get("/{campaign_id}")
def get_campaign_detail(
    campaign_id: int,
    db: Session = Depends(get_db),
    token_data: dict = Depends(verify_firebase_token)
):
    uid = token_data.get('uid')
    
    campaign = db.query(WhatsAppCampaign).filter(
        WhatsAppCampaign.id == campaign_id,
        WhatsAppCampaign.establishment_id == uid
    ).first()

    if not campaign:
        raise HTTPException(status_code=404, detail="Campaña no encontrada")

    return campaign # Aquí sí mandamos todo, incluyendo el JSON pesado de 'responses'


@router.post("/prepare-mass-send")
def prepare_mass_send(
    data: PrepareCampaignSchema, 
    db: Session = Depends(get_db),
    token_data: dict = Depends(verify_firebase_token)
):
    establishment_id = token_data.get('uid')
    
    # 1. QUERY CUSTOMERS
    query = db.query(Customer).filter(Customer.establishment_id == establishment_id)

    if data.tag_id != 0:
        # Use Postgres ANY operator for the tag array
        query = query.filter(text(f":tag_id = ANY(tag_ids)").bindparams(tag_id=data.tag_id))

    customers = query.all()

    if not customers:
        return {"message": "No customers found for selection", "total": 0}

    # 2. BULK PREPARATION
    new_dispatches = []
    for c in customers:
        if c.phone and c.country_code:
            full_number = int(f"{c.country_code}{c.phone}")
            
            new_dispatches.append(
                WhatsAppDispatch(
                    campaign_id=data.campaign_id,
                    phone_number=full_number,
                    customer_id=c.id,
                    country=c.country_name or "",
                    customer_name=c.first_name or "",
                    establishment_id=establishment_id
                )
            )

    # 3. BULK EXECUTION
    try:
        db.bulk_save_objects(new_dispatches)
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Bulk insert failed: {str(e)}")

    return {
        "status": "success",
        "total_prepared": len(new_dispatches),
        "message": f"Prepared {len(new_dispatches)} messages for campaign {data.campaign_id}"
    }