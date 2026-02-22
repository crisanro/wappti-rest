import pytz
from datetime import datetime, timedelta, timezone
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import text

from core.database import get_db
from core.auth import verify_superadmin_key  # Tu función que valida el header X-Superadmin-Key
from schemas.admin.appointment import AppointmentConfirmation, SingleUpdatePayload, WhatsAppStatusPayload

# 1. Configuración Global del Router
# Al poner las dependencias aquí, PROTEGES TODAS las funciones de este archivo automáticamente.
router = APIRouter(
    prefix="/admin",
    tags=["Admin Appointments"],
    dependencies=[Depends(verify_superadmin_key)] 
)

@router.get("/appointments/pending-batch", status_code=status.HTTP_200_OK)
async def get_pending_appointments_batch(
    hours_min: int = 8,
    hours_max: int = 21,
    db: Session = Depends(get_db)
):
    now_utc = datetime.now(timezone.utc)
    start_range = now_utc + timedelta(hours=hours_min)
    end_range = now_utc + timedelta(hours=hours_max)

    # Nota: 'e.language' es la columna real en tu tabla 'establishments'
    sql_query = text("""
        SELECT 
            a.id AS appo_id, a.appointment_date,
            c.first_name, c.country_code, c.phone, c.language AS customer_lang,
            p.timezone AS profile_tz, p.message_language AS location_info,
            e.id AS est_id, e.name AS est_name, e.available_credits,
            e.header_signature, e.virtual_assistant_signature, e.message_signature,
            e.language AS est_lang  -- Aquí mapeamos la columna real 'language' a 'est_lang'
        FROM appointments a
        INNER JOIN customers c ON a.customer_id = c.id
        INNER JOIN profiles p ON a.profile_id = p.id
        INNER JOIN establishments e ON a.establishment_id = e.id
        WHERE a.response_text = 'pending' 
          AND e.available_credits > 0
          AND e.is_suspended = FALSE
          AND a.appointment_date BETWEEN :start AND :end
        ORDER BY e.id, a.appointment_date ASC
    """)

    try:
        results = db.execute(sql_query, {"start": start_range, "end": end_range}).mappings().all()
        
        est_groups = {}
        for row in results:
            est_id = row["est_id"]
            if est_id not in est_groups:
                est_groups[est_id] = {"info": row, "items": []}
            est_groups[est_id]["items"].append(row)

        appointments_to_send = []
        business_alerts = []

        for est_id, group in est_groups.items():
            credits = group["info"]["available_credits"]
            total_requested = len(group["items"])
            
            # Lógica de estados
            if credits < total_requested:
                status_code = "INSUFFICIENT_CREDITS"
                allowed = credits
            elif credits == total_requested:
                status_code = "EXACT_CREDITS_REACHING_ZERO"
                allowed = total_requested
            else:
                remaining = credits - total_requested
                status_code = "LOW_CREDITS_WARNING" if remaining <= 10 else "HEALTHY"
                allowed = total_requested

            # Alertas para el dueño del local
            if status_code != "HEALTHY":
                business_alerts.append({
                    "establishment_id": est_id,
                    "establishment_name": group["info"]["est_name"],
                    "establishment_lang": group["info"]["est_lang"] or "es", # Idioma del local
                    "alert_code": status_code,
                    "credits_left": max(0, credits - allowed)
                })

            for i in range(allowed):
                row = group["items"][i]
                tz = pytz.timezone(row["profile_tz"] or "America/Guayaquil")
                local_dt = row["appointment_date"].astimezone(tz)
                now_local = datetime.now(tz)
                
                delta_days = (local_dt.date() - now_local.date()).days
                day_ref = "today" if delta_days == 0 else "tomorrow" if delta_days == 1 else local_dt.strftime("%d/%m")

                appointments_to_send.append({
                    "appointment_id": row["appo_id"],
                    "customer_name": row["first_name"],
                    "customer_whatsapp": f"{row['country_code']}{row['phone']}",
                    "customer_lang": row["customer_lang"] or "es",
                    "time_details": {
                        "local_date": local_dt.strftime("%Y-%m-%d"),
                        "local_time": local_dt.strftime("%H:%M"),
                        "day_ref": day_ref
                    },
                    "template_data": {
                        "header_text": (row["header_signature"] or row["est_name"])[:25],
                        "assistant_name": row["virtual_assistant_signature"] or "de Wappti",
                        "location_info": row["location_info"] or row["message_signature"] or row["est_name"]
                    },
                    "establishment_id": est_id,
                    "establishment_lang": row["est_lang"] or "es" # Enviamos el idioma del local también aquí
                })

        return {
            "status": "success",
            "appointments": appointments_to_send,
            "business_alerts": business_alerts
        }

    except Exception as e:
        print(f"❌ Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/update-single-send", status_code=status.HTTP_200_OK)
async def update_single_send(
    payload: SingleUpdatePayload, # Recibimos el JSON aquí
    db: Session = Depends(get_db),
    _ : str = Depends(verify_superadmin_key)
):
    """
    Actualiza cita y créditos mediante un cuerpo JSON.
    """
    try:
        # Usamos payload.campo para acceder a los datos
        query = text("""
            WITH updated_appo AS (
                UPDATE appointments 
                SET response_text = 'sent', whatsapp_id = :w_id 
                WHERE id = :a_id AND response_text = 'pending'
                RETURNING id
            )
            UPDATE establishments 
            SET available_credits = available_credits - 1
            WHERE id = :e_id AND EXISTS (SELECT 1 FROM updated_appo);
        """)
        
        result = db.execute(query, {
            "a_id": payload.appointment_id, 
            "w_id": payload.whatsapp_id, 
            "e_id": payload.establishment_id
        })
        
        db.commit()

        if result.rowcount == 0:
            return {
                "status": "already_processed", 
                "message": "La cita no existe, ya fue enviada o el local es incorrecto."
            }

        return {"status": "success"}

    except Exception as e:
        db.rollback()
        print(f"❌ Error actualizando cita {payload.appointment_id}: {e}")
        raise HTTPException(status_code=500, detail="SINGLE_UPDATE_FAILED")

@router.post("/process-whatsapp-status")
async def process_whatsapp_status(payload: WhatsAppStatusPayload, db: Session = Depends(get_db)):
    try:
        # 1. Buscar la cita por cualquiera de los dos IDs de WhatsApp
        query = text("""
            SELECT 
                a.id as appo_id, a.establishment_id, a.whatsapp_id, a.whatsapp_id_2,
                c.first_name as customer_name, e.language as est_lang
            FROM appointments a
            JOIN customers c ON a.customer_id = c.id
            JOIN establishments e ON a.establishment_id = e.id
            WHERE a.whatsapp_id = :w_id OR a.whatsapp_id_2 = :w_id
        """)
        row = db.execute(query, {"w_id": payload.whatsapp_id}).mappings().first()

        if not row:
            return {"case": "NOT_FOUND", "sub_case": "UNKNOWN_ID", "trigger_n8n": False}

        # --- CASO 1: Actualización de Estado (Viene 'status') ---
        if payload.status:
            # Actualización básica de estado en la tabla
            db.execute(
                text("UPDATE appointments SET whatsapp_status = :st WHERE id = :id"),
                {"st": payload.status, "id": row["appo_id"]}
            )

            # Sub-caso A: Éxito
            if payload.status in ["delivered", "read", "sent"]:
                db.commit()
                return {
                    "case": "STATUS_UPDATE",
                    "sub_case": "SUCCESS",
                    "trigger_n8n": True,
                    "data": {"status": payload.status, "appointment_id": row["appo_id"]}
                }

            # Sub-caso B: Fallo (Error)
            if payload.status == "failed":
                full_error = f"({payload.error_code}) {payload.error_title}"
                
                # Devolución de crédito y log de error
                db.execute(text("UPDATE establishments SET available_credits = available_credits + 1 WHERE id = :e_id"),
                           {"e_id": row["establishment_id"]})
                db.execute(text("INSERT INTO whatsapp_errors (appointment_id, error_message) VALUES (:a_id, :msg)"),
                           {"a_id": row["appo_id"], "msg": full_error})

                db.commit()

                # Discriminación de tipo de error
                sub_case = "FAILED_USER_NUMBER" if payload.error_code == "131026" else "FAILED_SYSTEM_ADMIN"
                return {
                    "case": "STATUS_UPDATE",
                    "sub_case": sub_case,
                    "trigger_n8n": True,
                    "data": {
                        "error_code": payload.error_code,
                        "error_title": payload.error_title,
                        "appointment_id": row["appo_id"]
                    }
                }

        # --- CASO 2 & 3: Respuesta del Cliente (Viene 'response_text') ---
        if payload.response_text:
            
            # CASO 2: Coincide con whatsapp_id (Confirmación / Reagendamiento)
            if payload.whatsapp_id == row["whatsapp_id"]:
                db.execute(
                    text("UPDATE appointments SET response_text = :txt WHERE id = :id"),
                    {"txt": payload.response_text, "id": row["appo_id"]}
                )
                db.commit()
                
                sub_case = "CUSTOMER_CONFIRMED" if payload.response_text.lower() == "confirmed" else "CUSTOMER_RESCHEDULED"
                return {
                    "case": "APPOINTMENT_RESPONSE",
                    "sub_case": sub_case,
                    "trigger_n8n": True,
                    "data": {"text": payload.response_text, "appointment_id": row["appo_id"]}
                }

            # CASO 3: Coincide con whatsapp_id_2 (Calidad del Servicio)
            elif payload.whatsapp_id == row["whatsapp_id_2"]:
                db.execute(
                    text("UPDATE appointments SET service_quality = :txt WHERE id = :id"),
                    {"txt": payload.response_text, "id": row["appo_id"]}
                )
                db.commit()

                # Determinamos sub-caso de calidad
                text_low = payload.response_text.lower()
                if "noshow" in text_low: sub_case = "QUALITY_NOSHOW"
                elif "good_service" in text_low: sub_case = "QUALITY_GOOD"
                else: sub_case = "QUALITY_COMPLAINT" # Para 'file_complaint' o cualquier otro

                return {
                    "case": "SERVICE_QUALITY_FEEDBACK",
                    "sub_case": sub_case,
                    "trigger_n8n": True,
                    "data": {"text": payload.response_text, "appointment_id": row["appo_id"]}
                }

        db.commit()
        return {"case": "OTHER_STATUS", "sub_case": "NO_ACTION_TAKEN", "trigger_n8n": False}

    except Exception as e:
        db.rollback()
        return {"case": "SYSTEM_ERROR", "sub_case": "EXCEPTION", "detail": str(e)}
