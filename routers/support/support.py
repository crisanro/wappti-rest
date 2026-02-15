from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
import pytz
import traceback
from sqlalchemy import and_, not_
from core.database import get_db
from core.auth import verify_firebase_token
from core.utils import register_action_log
from models import *
from datetime import datetime
from schemas.support import (
    FAQResponse,
    GrowthTipResponse,
    SystemAlertCreate,
    UserSuggestionCreate,
    ReviewCreate,        # <--- Este reemplaza a lo que antes era la Review
    SystemAuditResponse
)

router = APIRouter()

# --- SECTION: SYSTEM REPORTS & ALERTS (Protected) ---

@router.post("/alerts", dependencies=[Depends(verify_firebase_token)])
def create_incident_report(data: SystemAlertCreate, db: Session = Depends(get_db)):
    """
    Reports technical issues or system bugs.
    """
    new_alert = SystemAlert(
        type=data.type,
        email=data.email,
        user_id=data.user_id,
        is_verified=False
    )
    db.add(new_alert)
    db.commit()
    
    return {"message": "Report received successfully"}


@router.post("/report-payment-issue", dependencies=[Depends(verify_firebase_token)])
def create_payment_alert(data: SystemAlertCreate, db: Session = Depends(get_db)):
    """
    Reports specific issues related to payments or Infron logs.
    """
    new_alert = SystemAlert(
        type=data.type,
        email=data.email,
        user_id=data.user_id,
        is_verified=False
    )
    db.add(new_alert)
    db.commit()
    
    return {"status": "dispatched"}


# --- SECTION: REVIEWS (Protected Create, Public Feed) ---

@router.post("/reviews", status_code=status.HTTP_201_CREATED, dependencies=[Depends(verify_firebase_token)])
def create_review(data: ReviewCreate, db: Session = Depends(get_db)):
    """
    Saves a business review.
    """
    new_review = EstablishmentReview(**data.model_dump())
    db.add(new_review)
    db.commit()
    
    return {"status": "review saved"}


@router.get("/reviews")
def get_combined_reviews(
    tz_name: str = "America/Guayaquil", 
    db: Session = Depends(get_db),
    token_data: dict = Depends(verify_firebase_token)
):
    try:
        my_id = token_data.get('uid')
        
        try:
            local_tz = pytz.timezone(tz_name)
        except:
            local_tz = pytz.UTC

        def format_dt(dt):
            if not dt: return None
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=pytz.UTC)
            return dt.astimezone(local_tz).isoformat()

        # 1. Consulta: Mis Reseñas
        my_reviews_db = db.query(EstablishmentReview).filter(
            EstablishmentReview.establishment_id == my_id
        ).order_by(EstablishmentReview.created_at.desc()).all()

        # 2. Consulta: Feed Global (Excluyendo las mías)
        global_reviews_db = db.query(EstablishmentReview).filter(
            EstablishmentReview.establishment_id != my_id
        ).order_by(EstablishmentReview.created_at.desc()).limit(15).all()

        # 3. Respuesta limpia sin establishment_id
        return {
            "my_reviews": [
                {
                    "id": r.id,
                    "rating": r.rating,
                    "comment": r.comment,
                    "customer_name": r.customer_name,
                    "created_at": format_dt(r.created_at)
                } for r in my_reviews_db
            ],
            "global_feed": [
                {
                    "rating": r.rating,
                    "comment": r.comment,
                    "customer_name": r.customer_name,
                    "created_at": format_dt(r.created_at)
                } for r in global_reviews_db
            ]
        }

    except Exception as e:
        import traceback
        print("--- DEBUG ERROR EN REVIEWS ---")
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail="Error al cargar reseñas")
    
# --- SECTION: CONTENT & HELP (Public for easier support) ---

@router.get("/faq")
def get_frequently_asked_questions(db: Session = Depends(get_db)):
    """
    Retorna la lista de preguntas frecuentes ordenada por display_order.
    """
    try:
        # Usamos display_order que es el nombre real de tu columna
        faqs = db.query(FAQ).order_by(FAQ.display_order.asc()).all()
        
        # Lo devolvemos de forma explícita para asegurar que no haya errores de serialización
        return [
            {
                "id": f.id,
                "question": f.question,
                "answer": f.answer,
                "display_order": f.display_order
            } for f in faqs
        ]
        
    except Exception as e:
        import traceback
        print(f"--- ERROR EN FAQ ---")
        print(traceback.format_exc())
        # Esto te devolverá el error real en el detail si vuelve a fallar
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")


@router.get("/tips")
def get_growth_tips(db: Session = Depends(get_db)):
    """
    Returns tips for business growth.
    """
    return db.query(GrowthTip).order_by(GrowthTip.created_at.desc()).all()


# --- SECTION: USER FEEDBACK (Protected) ---
@router.get("/suggestions")
def get_user_suggestions(
    tz_name: str = "America/Guayaquil",
    db: Session = Depends(get_db), 
    token_data: dict = Depends(verify_firebase_token)
):
    try:
        establishment_id = token_data.get('uid')
        
        try:
            local_tz = pytz.timezone(tz_name)
        except:
            local_tz = pytz.UTC

        # Consultamos las sugerencias de este establecimiento
        suggestions_db = db.query(UserSuggestion).filter(
            UserSuggestion.establishment_id == establishment_id
        ).order_by(UserSuggestion.created_at.desc()).all()

        result = []
        for s in suggestions_db:
            # Formatear fecha
            dt = s.created_at
            if dt and dt.tzinfo is None:
                dt = dt.replace(tzinfo=pytz.UTC)
            
            local_date = dt.astimezone(local_tz).isoformat() if dt else None

            result.append({
                "id": s.id,
                "suggestion": s.suggestion,
                "response": s.response, # Aquí verá lo que tú le respondas desde la DB
                "created_at": local_date
            })
            
        return result

    except Exception as e:
        print(f"🚨 ERROR GET SUGGESTIONS: {str(e)}")
        raise HTTPException(status_code=500, detail="error_fetching_suggestions")
    

@router.post("/suggestions")
def create_user_suggestion(
    data: UserSuggestionCreate, 
    db: Session = Depends(get_db), 
    token_data: dict = Depends(verify_firebase_token)
):
    try:
        establishment_id = token_data.get('uid')

        # El error estaba aquí: cambiamos data.suggestion por data.suggestion_text
        new_suggestion = UserSuggestion(
            suggestion=data.suggestion_text,  # <--- Mapeo correcto
            establishment_id=establishment_id,
            response=None,
            created_at=datetime.now(pytz.UTC)
        )
        
        db.add(new_suggestion)
        db.commit()
        db.refresh(new_suggestion)
        
        return {"status": "success", "id": new_suggestion.id}

    except Exception as e:
        db.rollback()
        import traceback
        print(f"🚨 ERROR: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"Error interno: {str(e)}")