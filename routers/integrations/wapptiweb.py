from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import desc
from core.database import get_db
# Asumo que tienes tu modelo de SQLAlchemy definido en core.models
from models import EstablishmentReview 
from schemas.integrations import ReviewOut
from core.auth import verify_internal_key

router = APIRouter()


BANNED_WORDS = ["mierda", "pendejo", "estafa", "puto", "basura"] 

def sanitize_comment(text: str) -> str:
    if not text: return ""
    clean_text = text
    for word in BANNED_WORDS:
        # Reemplazo insensible a mayúsculas
        if word.lower() in clean_text.lower():
            clean_text = clean_text.replace(word, "****")
    return clean_text

# --- 3. El Endpoint ---
@router.get("/latest-reviews", response_model=list[ReviewOut])
def get_latest_reviews(
    db: Session = Depends(get_db), 
    # Ahora usamos la validación de la llave del Proxy
    _security: bool = Depends(verify_internal_key) 
):
    reviews = (
        db.query(EstablishmentReview)
        .order_by(desc(EstablishmentReview.created_at))
        .limit(10)
        .all()
    )

    for r in reviews:
        r.comment = sanitize_comment(r.comment)
        r.customer_name = sanitize_comment(r.customer_name)

    return reviews
