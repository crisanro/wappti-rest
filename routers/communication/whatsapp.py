from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session
from sqlalchemy import and_, text
from datetime import datetime, timedelta, timezone
import traceback
import json
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
    PrepareCampaignSchema, UpdateCampaignResponse
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
    

@router.patch("/{campaign_id}")
def update_campaign_responses(
    campaign_id: int, 
    payload: UpdateCampaignResponse,
    request: Request,
    db: Session = Depends(get_db),
    token_data: dict = Depends(verify_firebase_token)
):
    """
    Updates the 'responses' JSONB column for a specific campaign.
    Ensures the campaign belongs to the authenticated establishment.
    """
    establishment_id = token_data.get('uid')

    try:
        # 1. Update with Campaign ID and Establishment ID for security
        query = text("""
            UPDATE whatsapp_campaigns 
            SET responses = :new_json
            WHERE id = :c_id AND establishment_id = :e_id
            RETURNING id
        """)

        result = db.execute(query, {
            "new_json": json.dumps(payload.responses),
            "c_id": campaign_id,
            "e_id": establishment_id
        })
        
        # Check if any row was actually updated
        if not result.fetchone():
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, 
                detail="Access denied or campaign not found."
            )

        # 2. Audit log
        register_action_log(
            db=db,
            establishment_id=establishment_id,
            action="CAMPAIGN_RESPONSES_UPDATED",
            method="PATCH",
            path=request.url.path,
            payload={"campaign_id": campaign_id},
            request=request
        )

        db.commit()
        return {
            "status": "success", 
            "message": f"Campaign {campaign_id} responses updated successfully."
        }

    except HTTPException as he:
        raise he
    except Exception as e:
        db.rollback()
        # Log error for internal tracking
        print(f"🚨 CRITICAL ERROR: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
            detail="Internal server error during campaign update."
        )


@router.get("/{campaign_id}/dispatches")
def get_campaign_dispatches(
    campaign_id: int,
    db: Session = Depends(get_db),
    token_data: dict = Depends(verify_firebase_token)
):
    """
    Retrieves campaign dispatches. 
    Validates that the authenticated establishment owns the campaign.
    Columns: phone_number, status, customer_id
    """
    establishment_id = token_data.get('uid')

    # 1. Query with JOIN to validate ownership across tables
    # We ensure dispatches are only shown if the campaign's establishment_id matches the JWT
    query = text("""
        SELECT 
            d.phone_number, 
            d.status, 
            d.customer_id
        FROM whatsapp_dispatches d
        JOIN whatsapp_campaigns c ON d.campaign_id = c.id
        WHERE d.campaign_id = :c_id 
          AND c.establishment_id = :e_id
    """)

    try:
        results = db.execute(query, {
            "c_id": campaign_id,
            "e_id": establishment_id
        }).mappings().all()

        # 2. Return empty list if no records found or access is restricted
        # (The JOIN handles the security implicitly)
        if not results:
            return []

        return results

    except Exception as e:
        # Internal logging for debugging
        print(f"🚨 DISPATCH FETCH ERROR: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
            detail="Internal server error while retrieving dispatches."
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


