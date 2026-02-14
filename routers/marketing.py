from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status, Query
from fastapi.encoders import jsonable_encoder
from datetime import datetime, timezone, date
from sqlalchemy.sql import func
from sqlalchemy.orm import Session
from sqlalchemy import and_, cast, Date
import time
from core.database import get_db
from core.auth import verify_firebase_token
from core.utils import register_action_log

# Import English models
from models import *

# Import updated schemas
from schemas.business import (
    EstablishmentInfo, 
    EstablishmentUpdate, 
    PinUpdate, 
    ProfileResponse,
    ProfileCreate,
    ProfileBase, 
    ProfileUpdate,
    TutorialLinkResponse,
    CalendarNoteResponse,
    CalendarNoteCreate
)

from pydantic import ValidationError
from traceback import print_exc
import random

# Apply global security to the business router
router = APIRouter(dependencies=[Depends(verify_firebase_token)])


def update_ad_views_task(ad_id: int):
    # Obtenemos la sesión llamando al generador y extrayendo el valor
    db_gen = get_db()
    db = next(db_gen) 
    try:
        db.query(AppAd).filter(AppAd.id == ad_id).update(
            {"views_count": AppAd.views_count + 1}
        )
        db.commit()
    except Exception as e:
        db.rollback()
        print(f"Error actualizando vistas: {e}")
    finally:
        # Cerramos la sesión manualmente ya que estamos fuera del flujo normal de FastAPI
        try:
            next(db_gen) # Esto ejecuta el bloque 'finally' de tu get_db
        except StopIteration:
            pass

@router.get("/ads")
async def get_balanced_advertisements(
    background_tasks: BackgroundTasks, 
    db: Session = Depends(get_db)
):
    try:
        # 1. Obtenemos el mínimo de vistas de forma eficiente
        min_views = db.query(func.min(AppAd.views_count)).scalar() or 0
        
        # 2. Traemos una pequeña lista de candidatos (ej. los primeros 10) 
        # en lugar de pedirle a la DB que los ordene aleatoriamente todos.
        candidates = db.query(AppAd).filter(
            AppAd.views_count <= (min_views + 2)
        ).limit(10).all()

        if not candidates:
            raise HTTPException(status_code=404, detail="no_ads_available")

        # 3. La aleatoriedad la hace Python (es casi instantáneo)
        ad = random.choice(candidates)

        # 4. TRUCO DE VELOCIDAD: Actualizamos vistas en SEGUNDO PLANO
        # Pasamos la función de update para que se ejecute después del return
        background_tasks.add_task(update_ad_views_task, ad.id)

        # 5. Respuesta inmediata (sin db.refresh)
        return {
            "id": ad.id,
            "title": ad.title,
            "description": ad.description,
            "image_url": ad.image_url,
            "cta_url": ad.cta_url,
            "hex_color": ad.hex_color,
            "views_count": ad.views_count + 1, # Simulamos el +1 para que el cliente lo vea
            "internal_name": ad.internal_name
        }

    except Exception as e:
        # No hace falta rollback aquí porque no hemos iniciado transacciones de escritura aún
        raise HTTPException(
            status_code=500, 
            detail={"error": "advertisement_fetch_error", "debug_info": str(e)}
        )


@router.get("/tutorial/{link_id}", response_model=TutorialLinkResponse)
def get_tutorial_link(link_id: int, db: Session = Depends(get_db)):
    # Buscamos el registro
    link = db.query(TutorialLink).filter(TutorialLink.id == link_id).first()
    
    # Manejo de error en inglés y snake_case
    if not link:
        raise HTTPException(
            status_code=404, 
            detail="not_found"
        )
    
    return link

@router.get("/test-speed")
async def test_speed():
    start_time = time.time()
    # Simulamos un proceso ultra rápido sin DB
    execution_time = (time.time() - start_time) * 1000
    return {"message": "FastAPI is alive", "server_time_ms": execution_time}