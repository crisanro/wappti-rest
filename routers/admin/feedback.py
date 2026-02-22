import os
import traceback
from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from models import CustomerFeedback, Appointment
from core.database import get_db
from core.auth import verify_superadmin_key  # Tu función que valida el header X-Superadmin-Key
from schemas.admin.notification import CustomerFeedback, Appointment

router = APIRouter(prefix="/feedback", tags=["Customer Feedback"])


# --- 1. ADMIN: CREAR FILA (POST) ---
@router.post("/admin/create-row", dependencies=[Depends(verify_superadmin_key)])
async def create_feedback_row(data: CreateFeedbackRowSchema, db: Session = Depends(get_db)):
    """
    Crea la entrada inicial vinculada al ID del appointment.
    Solo accesible via SuperAdmin Key.
    """
    # Verificamos si ya existe para evitar duplicados
    existing = db.query(CustomerFeedback).filter(CustomerFeedback.id == data.appointment_id).first()
    if existing:
        raise HTTPException(status_code=400, detail="FEEDBACK_ROW_ALREADY_EXISTS")
    
    try:
        new_row = CustomerFeedback(
            id=data.appointment_id,
            establishment_signature=data.establishment_signature,
            created_at=datetime.now(timezone.utc),
            complaint=None  # Iniciamos vacío
        )
        db.add(new_row)
        db.commit()
        return {"status": "success", "id": data.appointment_id}
    except Exception as e:
        db.rollback()
        print(f"🚨 Error creando row de feedback: {str(e)}")
        raise HTTPException(status_code=500, detail="INTERNAL_SERVER_ERROR")


# --- 2. PÚBLICO: LECTURA (GET) ---
@router.get("/public/{feedback_id}")
async def get_feedback_status(feedback_id: str, db: Session = Depends(get_db)):
    """
    Endpoint abierto para la App/Web de cliente.
    Bloquea el acceso si 'complaint' ya tiene contenido.
    """
    feedback = db.query(CustomerFeedback).filter(CustomerFeedback.id == feedback_id).first()
    
    if not feedback:
        raise HTTPException(status_code=404, detail="FEEDBACK_NOT_FOUND")
    
    # SEGURIDAD: Si ya hay una queja, el link "muere" para el usuario
    if feedback.complaint and feedback.complaint.strip():
        raise HTTPException(status_code=403, detail="LINK_EXPIRED_OR_ALREADY_SUBMITTED")
    
    return {
        "id": feedback.id,
        "establishment_signature": feedback.establishment_signature,
        "can_write": True
    }


# --- 3. PÚBLICO: ESCRITURA (POST) ---
@router.post("/public/{feedback_id}/submit")
async def submit_complaint(feedback_id: str, data: SubmitComplaintSchema, db: Session = Depends(get_db)):
    """
    Endpoint abierto para que el usuario envíe su queja.
    Solo permite escribir si 'complaint' está NULL o vacío.
    """
    feedback = db.query(CustomerFeedback).filter(CustomerFeedback.id == feedback_id).first()
    
    if not feedback:
        raise HTTPException(status_code=404, detail="FEEDBACK_NOT_FOUND")
    
    # SEGURIDAD CRÍTICA: Bloqueo de re-escritura
    if feedback.complaint and feedback.complaint.strip():
        raise HTTPException(status_code=403, detail="SUBMISSION_LOCKED")
    
    try:
        feedback.complaint = data.complaint
        # Aquí podrías añadir un campo 'updated_at' si lo tuvieras en la tabla
        db.commit()
        
        return {
            "status": "success", 
            "message": "Feedback submitted successfully"
        }
    except Exception as e:
        db.rollback()
        print(f"🚨 Error al enviar complaint: {str(e)}")
        raise HTTPException(status_code=500, detail="INTERNAL_SERVER_ERROR")
