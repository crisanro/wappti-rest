from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import text
from pydantic import BaseModel
from typing import Optional

# Tus imports de autenticación y base de datos
from core.database import get_db
from core.auth import verify_superadmin_key
from models import UsageAuditLog, Appointment
from schemas.admin.appointment import AppointmentReminderUpdate
from schemas.admin.establishments import AuditLogCreate

router = APIRouter()


@router.post("/audit-log", status_code=status.HTTP_201_CREATED)
async def create_audit_log(
    payload: AuditLogCreate,
    db: Session = Depends(get_db),
    _: str = Depends(verify_superadmin_key) # Protegido
):
    """
    Crea un registro manual en el historial de transacciones/auditoría.
    """
    try:
        new_log = UsageAuditLog(
            establishment_id=payload.establishment_id,
            condition=payload.condition,
            value=payload.value,
            observations=payload.observations
        )
        db.add(new_log)
        db.commit()
        db.refresh(new_log)
        return {"status": "success", "log_id": new_log.id}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"LOG_CREATION_FAILED: {str(e)}")


@router.patch("/update-appointment-reminder/{appointment_id}", status_code=status.HTTP_200_OK)
async def update_appointment_reminder(
    appointment_id: int,
    payload: AppointmentReminderUpdate,
    db: Session = Depends(get_db),
    _: str = Depends(verify_superadmin_key) # Protegido
):
    """
    Actualiza el ID de WhatsApp del recordatorio (cuando no han respondido al principal).
    """
    try:
        # Buscamos la cita
        appointment = db.query(Appointment).filter(Appointment.id == appointment_id).first()
        
        if not appointment:
            raise HTTPException(status_code=404, detail="APPOINTMENT_NOT_FOUND")

        # Actualizamos la nueva columna
        appointment.whatsapp_id_reminder = payload.whatsapp_id_reminder
        
        db.commit()
        return {"status": "success", "message": "Reminder ID updated"}
        
    except HTTPException as he:
        raise he
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"UPDATE_FAILED: {str(e)}")