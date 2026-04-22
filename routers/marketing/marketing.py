from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status, Query
from fastapi.encoders import jsonable_encoder
from datetime import datetime, timezone, date
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Session
from sqlalchemy import and_, cast, Date, func, or_, select, cast, Text
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


# --- BACKGROUND TASKS ---

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
        # Cerramos la sesión manualmente
        try:
            next(db_gen) 
        except StopIteration:
            pass

def process_ad_click_task(ad_id: int, establishment_id: str):
    """Suma 1 al contador global y guarda el log de quién hizo clic"""
    db_gen = get_db()
    db = next(db_gen) 
    try:
        # 1. Actualizar contador global
        db.query(AppAd).filter(AppAd.id == ad_id).update(
            {"clicks_count": AppAd.clicks_count + 1}
        )
        
        # 2. Registrar quién hizo clic
        new_click_log = AppAdClick(
            ad_id=ad_id,
            establishment_id=establishment_id
        )
        db.add(new_click_log)
        
        # 3. Guardar todo
        db.commit()
    except Exception as e:
        db.rollback()
        print(f"Error registrando el clic en BD: {e}")
    finally:
        try:
            next(db_gen) 
        except StopIteration:
            pass


# --- ENDPOINTS ---

@router.get("/ads")
async def get_balanced_advertisements(
    background_tasks: BackgroundTasks, 
    db: Session = Depends(get_db),
    token_data: dict = Depends(verify_firebase_token)
):
    try:
        est_id = token_data.get("uid")
        
        # 1. Obtener país
        user_country = db.query(Establishment.country).filter(Establishment.id == est_id).scalar()

        # 2. Subquery de proveedores (Corregido para evitar warnings)
        # Obtenemos la lista directamente para simplificar la query principal
        used_providers = db.query(func.lower(EstablishmentToken.provider)).filter(
            EstablishmentToken.establishment_id == est_id
        ).all()
        used_providers_list = [p[0] for p in used_providers]

        # 3. Query principal con filtros de exclusión
        query = db.query(AppAd).filter(
            ~func.lower(AppAd.internal_name).in_(used_providers_list)
        )

        # 4. Filtro de País con CAST a ARRAY(Text)
        if user_country:
            country_filter = or_(
                AppAd.target_countries.contains(cast(["all"], ARRAY(Text))),
                AppAd.target_countries.contains(cast([user_country], ARRAY(Text)))
            )
        else:
            country_filter = AppAd.target_countries.contains(cast(["all"], ARRAY(Text)))
            
        query = query.filter(country_filter)

        # 5. Obtener mínimo de vistas (Forma simplificada para evitar errores de anon_1)
        # Ejecutamos una query simple basada en los filtros anteriores
        min_views = db.query(func.min(AppAd.views_count)).filter(
            AppAd.id.in_(db.query(query.subquery().c.id))
        ).scalar() or 0
        
        # 6. Candidatos
        candidates = query.filter(
            AppAd.views_count <= (min_views + 2)
        ).limit(10).all()

        if not candidates:
            raise HTTPException(status_code=404, detail="no_ads_available")

        ad = random.choice(candidates)
        background_tasks.add_task(update_ad_views_task, ad.id)

        return {
            "id": ad.id,
            "title": ad.title,
            "description": ad.description,
            "image_url": ad.image_url,
            "click_proxy_url": f"/ads/{ad.id}/click", 
            "hex_color": ad.hex_color,
            "views_count": ad.views_count + 1,
            "internal_name": ad.internal_name
        }

    except Exception as e:
        print_exc()
        raise HTTPException(
            status_code=500, 
            detail={"error": "advertisement_fetch_error", "debug_info": str(e)}
        )


@router.get("/ads/{ad_id}/click") # Te sugiero /ads/{ad_id}/click, pero lo dejo como lo tienes
async def register_click_and_get_url(
    ad_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    token_data: dict = Depends(verify_firebase_token) 
):
    """
    Endpoint intermedio para registrar el clic antes de devolver la URL real.
    """
    # 1. ¡NUEVO!: Extraemos el ID de quien hace clic
    est_id = token_data.get("uid")

    # 2. Obtenemos solo la URL de destino
    ad_url = db.query(AppAd.cta_url).filter(AppAd.id == ad_id).scalar()
    
    if not ad_url:
        raise HTTPException(status_code=404, detail="ad_not_found")

    # 3. ¡NUEVO!: Le pasamos el ad_id Y TAMBIÉN el est_id a la tarea
    background_tasks.add_task(process_ad_click_task, ad_id, est_id)

    # 4. Retornamos la URL
    return {"cta_url": ad_url}


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