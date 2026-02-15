from fastapi import APIRouter, Depends, HTTPException, status, Query, Request
from fastapi.encoders import jsonable_encoder
from datetime import datetime, timezone, date
from sqlalchemy.sql import func
from sqlalchemy.orm import Session
from sqlalchemy import and_, cast, Date

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
# Apply global security to the business router
router = APIRouter(dependencies=[Depends(verify_firebase_token)])


# --- BUSCAR POR RANGO DE FECHAS ---
@router.get("/", response_model=list[CalendarNoteResponse])
def get_calendar_notes(
    target_date: date = Query(..., example="2026-02-11"),
    profile_id: int = Query(..., example=350), # Filtro obligatorio por perfil
    db: Session = Depends(get_db),
    token_data: dict = Depends(verify_firebase_token)
):
    try:
        establishment_id = token_data.get('uid')
        
        # Filtramos por establecimiento, perfil y fecha exacta
        notes = db.query(CalendarNote).filter(
            and_(
                CalendarNote.establishment_id == establishment_id,
                CalendarNote.profile_id == profile_id, # Filtro por ID de perfil
                cast(CalendarNote.event_date, Date) == target_date
            )
        ).order_by(CalendarNote.event_date.asc()).all()
        
        return notes

    except Exception as e:
        print(f"🚨 CALENDAR PROFILE FETCH ERROR: {str(e)}")
        raise HTTPException(
            status_code=500, 
            detail={
                "error": "calendar_notes_by_profile_failed",
                "debug_info": str(e)
            }
        )
    

@router.post("/", response_model=CalendarNoteResponse)
def create_calendar_note(
    data: CalendarNoteCreate, 
    request: Request, # Agregado para el log
    db: Session = Depends(get_db), 
    token_data: dict = Depends(verify_firebase_token)
):
    establishment_id = token_data.get('uid')
    try:
        new_note = CalendarNote(
            **data.model_dump(),
            establishment_id=establishment_id
        )
        db.add(new_note)
        db.commit()
        db.refresh(new_note)

        # Registro de Log y Heartbeat
        register_action_log(
            db=db,
            establishment_id=establishment_id,
            action="CALENDAR_NOTE_CREATE",
            method="POST",
            path="/notes/",
            payload={"note_id": new_note.id, "title": getattr(new_note, 'title', 'New Note')},
            request=request
        )

        return new_note
    except Exception as e:
        db.rollback()
        print(f"🚨 Error: {str(e)}")
        raise HTTPException(status_code=500, detail="calendar_note_creation_error")


# --- ELIMINAR NOTA ---
@router.delete("/{note_id}")
def delete_calendar_note(
    note_id: int, 
    request: Request, # Agregado para el log
    db: Session = Depends(get_db), 
    token_data: dict = Depends(verify_firebase_token)
):
    establishment_id = token_data.get('uid')
    
    note = db.query(CalendarNote).filter(
        CalendarNote.id == note_id,
        CalendarNote.establishment_id == establishment_id
    ).first()
    
    if not note:
        raise HTTPException(status_code=404, detail="calendar_note_not_found")
    
    try:
        # Registrar el log ANTES de borrar para tener la referencia
        register_action_log(
            db=db,
            establishment_id=establishment_id,
            action="CALENDAR_NOTE_DELETE",
            method="DELETE",
            path=f"/notes/{note_id}",
            payload={"deleted_note_id": note_id},
            request=request
        )

        db.delete(note)
        db.commit()
        return {"status": "success", "deleted_id": note_id}
        
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail="calendar_note_deletion_error")