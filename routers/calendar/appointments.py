
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_, func
from datetime import datetime, timezone, timedelta, time
import pytz
from typing import Optional, List
import traceback

from core.database import get_db
from core.auth import verify_firebase_token
from core.utils import register_action_log

# Import English models
from models import *
from fastapi.encoders import jsonable_encoder
# Import updated schemas
from schemas.operations import (
    CustomerHistoryCreate, 
    AppointmentCreate, 
    UsageAuditLogCreate, AppointmentUpdate
)
from schemas.users import TagResponse

router = APIRouter(dependencies=[Depends(verify_firebase_token)])

@router.get("/")
def get_appointments(
    start_date: str, 
    end_date: str, 
    tz_name: str = "America/Guayaquil",
    only_whatsapp: bool = False,
    profile_id: Optional[int] = None,
    db: Session = Depends(get_db),
    token_data: dict = Depends(verify_firebase_token)
):
    establishment_id = token_data.get('uid')

    try:
        # 1. Configurar Zona Horaria
        try:
            local_tz = pytz.timezone(tz_name)
        except:
            local_tz = pytz.UTC

        # 2. Procesar Fechas
        d_start = datetime.strptime(start_date, "%Y-%m-%d")
        d_end = datetime.strptime(end_date, "%Y-%m-%d")
        
        start_dt = local_tz.localize(datetime.combine(d_start, time.min))
        end_dt = local_tz.localize(datetime.combine(d_end, time.max))
        
        if (end_dt - start_dt).days > 45:
            raise HTTPException(status_code=400, detail="range_too_long_max_45_days")

        # 3. Query Directa (Ya no necesitamos el Join con Customer)
        query = db.query(Appointment).filter(
            Appointment.establishment_id == establishment_id,
            Appointment.appointment_date >= start_dt.astimezone(pytz.UTC),
            Appointment.appointment_date <= end_dt.astimezone(pytz.UTC)
        )

        # 4. Lógica de filtrado
        if only_whatsapp:
            query = query.filter(Appointment.whatsapp_id.isnot(None))
        else:
            if not profile_id:
                raise HTTPException(status_code=400, detail="profile_id_required_for_calendar_view")
            query = query.filter(Appointment.profile_id == profile_id)

        # 5. Orden Ascendente (De más reciente a más antiguo)
        appointments = query.order_by(Appointment.appointment_date.asc()).all()

        # 6. Respuesta formateada con IDs para mapeo local
        result = []
        for a in appointments:
            db_date = a.appointment_date.replace(tzinfo=pytz.UTC) if a.appointment_date.tzinfo is None else a.appointment_date
            
            result.append({
                "id": a.id,
                "customer_id": a.customer_id, # <--- Clave para tu lógica en memoria
                "profile_id": a.profile_id,
                "whatsapp_status": a.whatsapp_status,
                "response_text": a.response_text,
                "appointment_date": db_date.astimezone(local_tz).isoformat(),
                "reason": a.reason
            })
        
        return result

    except HTTPException as he:
        raise he
    except Exception as e:
        import traceback
        print(f"🚨 ERROR EN LISTA CITAS: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail="internal_server_error")


@router.post("/", status_code=201)
def insert_appointment(
    data: AppointmentCreate, 
    request: Request, 
    db: Session = Depends(get_db), 
    token_data: dict = Depends(verify_firebase_token)
):
    try:
        establishment_id = token_data.get('uid')

        # --- VALIDACIONES DE SEGURIDAD ---
        
        # 1. Validar que el Perfil (Profile) pertenezca al establecimiento
        # Asumiendo que tu tabla de perfiles tiene la columna establishment_id
        profile_check = db.query(Profile).filter(
            Profile.id == data.profile_id, 
            Profile.establishment_id == establishment_id
        ).first()
        
        if not profile_check:
            raise HTTPException(status_code=403, detail="profile_not_owned_by_user")

        # 2. Validar que el Cliente (Customer) pertenezca al establecimiento
        # Asumiendo que tu tabla de clientes tiene la columna establishment_id
        customer_check = db.query(Customer).filter(
            Customer.id == data.customer_id, 
            Customer.establishment_id == establishment_id
        ).first()
        
        if not customer_check:
            raise HTTPException(status_code=403, detail="customer_not_owned_by_user")

        # --- FIN DE VALIDACIONES ---

        # 1. Manejo de Zona Horaria
        user_tz = pytz.timezone(data.timezone_region)
        naive_date = data.appointment_date.replace(tzinfo=None)
        localized_date = user_tz.localize(naive_date)
        utc_date = localized_date.astimezone(pytz.UTC)

        # 2. Creación del objeto Appointment
        new_appointment = Appointment(
            **data.model_dump(exclude={"appointment_date", "timezone_region"}),
            appointment_date=utc_date,
            establishment_id=establishment_id,
            created_at=datetime.now(pytz.UTC),
            response_text="pending",
            whatsapp_status=None # Aprovechamos para inicializar el status de whatsapp
        )

        db.add(new_appointment)
        db.flush() 

        # 3. LOG DE AUDITORÍA
        register_action_log(
            db=db,
            establishment_id=establishment_id,
            action="CREATE_APPOINTMENT",
            method="POST",
            path=request.url.path,
            payload={
                "appointment_id": new_appointment.id,
                "customer_id": data.customer_id,
                "profile_id": data.profile_id,
                "date_utc": utc_date.isoformat(),
            },
            request=request
        )

        db.commit()
        db.refresh(new_appointment)

        return {"status": "success", "id": new_appointment.id}

    except HTTPException as he:
        # Re-lanzamos las excepciones de validación (403)
        raise he
    except Exception as e:
        db.rollback()
        import traceback
        print(f"🚨 ERROR REAL EN APPOINTMENT: {traceback.format_exc()}") 
        raise HTTPException(status_code=500, detail="internal_server_error_appointment")


@router.get("/upcoming")
def get_upcoming_appointments(
    profile_id: int,
    tz_name: str = "America/Guayaquil",
    db: Session = Depends(get_db),
    token_data: dict = Depends(verify_firebase_token)
):
    """
    Obtiene las próximas 10 citas incluyendo el texto de respuesta y estado de WhatsApp.
    """
    establishment_id = token_data.get('uid')

    try:
        # 1. Configurar Zona Horaria
        try:
            local_tz = pytz.timezone(tz_name)
        except Exception:
            local_tz = pytz.UTC

        # Tiempo base para la consulta
        now_local = datetime.now(local_tz)
        start_lookup_utc = (now_local - timedelta(minutes=30)).astimezone(pytz.UTC)

        # 2. Consulta a la DB
        appointments = db.query(Appointment).filter(
            and_(
                Appointment.establishment_id == establishment_id,
                Appointment.profile_id == profile_id,
                Appointment.appointment_date >= start_lookup_utc
            )
        ).order_by(Appointment.appointment_date.asc()).limit(10).all()

        # 3. Formatear Respuesta
        result = []
        for a in appointments:
            db_date = a.appointment_date
            if db_date.tzinfo is None:
                db_date = db_date.replace(tzinfo=pytz.UTC)
            
            local_date = db_date.astimezone(local_tz)

            result.append({
                "id": a.id,
                "customer_id": a.customer_id,
                "appointment_date": local_date.isoformat(),
                "reason": a.reason,
                "whatsapp_id": a.whatsapp_id,
                # --- NUEVOS CAMPOS ---
                "whatsapp_status": a.whatsapp_status, # String de la DB
                "response_text": a.response_text,     # String de la DB
                # ---------------------
                "minutes_from_now": int((local_date - now_local).total_seconds() / 60)
            })
        
        return result

    except Exception as e:
        import traceback
        print(f"🚨 ERROR EN UPCOMING: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail="error_fetching_upcoming_appointments")


@router.get("/{customer_id}")
def get_customer_appointments_history(
    customer_id: int,
    tz_name: str = "America/Guayaquil",
    db: Session = Depends(get_db),
    token_data: dict = Depends(verify_firebase_token)
):
    """
    Obtiene el historial detallado de un cliente con calidad de servicio y quejas.
    """
    establishment_id = token_data.get('uid')

    try:
        # 1. Zona Horaria
        try:
            local_tz = pytz.timezone(tz_name)
        except:
            local_tz = pytz.UTC

        # 2. Consulta filtrada por establecimiento y cliente
        appointments = db.query(Appointment).filter(
            Appointment.establishment_id == establishment_id,
            Appointment.customer_id == customer_id
        ).order_by(Appointment.appointment_date.desc()).all()

        # 3. Formatear Respuesta con tus columnas exactas
        result = []
        for a in appointments:
            db_date = a.appointment_date
            if db_date and db_date.tzinfo is None:
                db_date = db_date.replace(tzinfo=pytz.UTC)
            
            local_date = db_date.astimezone(local_tz).isoformat() if db_date else None

            result.append({
                "id": a.id,
                "customer_id": a.customer_id,
                "profile_id": a.profile_id,
                "appointment_date": local_date,
                "reason": a.reason,
                "response_text": a.response_text,
                "whatsapp_id": a.whatsapp_id,
                "whatsapp_status": a.whatsapp_status,
                "service_quality": a.service_quality, # <-- Nueva
                "complaint": a.complaint             # <-- Nueva
            })
        
        return result

    except Exception as e:
        import traceback
        print(f"🚨 ERROR EN HISTORIAL DETALLADO: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail="error_fetching_customer_history")
    

@router.patch("/{appointment_id}")
def update_appointment(
    appointment_id: int,
    data: AppointmentUpdate,
    request: Request,
    db: Session = Depends(get_db),
    token_data: dict = Depends(verify_firebase_token)
):
    uid = token_data.get('uid')

    # 1. Buscar la cita
    appointment = db.query(Appointment).filter(
        Appointment.id == appointment_id,
        Appointment.establishment_id == uid
    ).first()

    if not appointment:
        raise HTTPException(status_code=404, detail="appointment_not_found")

    # 2. Extraer solo los campos que el cliente ENVIÓ en el JSON (exclude_unset=True)
    raw_update_data = data.model_dump(exclude_unset=True)

    # 3. FILTRADO DINÁMICO: Eliminar los que son None o strings vacíos
    # Esto asegura que si mandan {"name": ""} o {"note": null}, no se sobreescriba lo que ya existe
    update_data = {
        k: v for k, v in raw_update_data.items() 
        if v is not None and v != ""
    }

    # Si después del filtro no queda nada que actualizar, respondemos temprano
    if not update_data:
        return {"status": "no_changes", "message": "No valid data provided for update"}

    # 4. Lógica especial para la fecha (solo si viene en el update_data filtrado)
    if "appointment_date" in update_data:
        if not data.timezone_region:
            raise HTTPException(status_code=400, detail="timezone_region_required_to_update_date")
        
        try:
            user_tz = pytz.timezone(data.timezone_region)
            # El objeto ya viene como datetime desde Pydantic
            dt = update_data["appointment_date"]
            
            # Quitar tzinfo si viene de Pydantic para localizarlo manualmente al timezone del usuario
            if dt.tzinfo is not None:
                dt = dt.replace(tzinfo=None)
                
            localized_date = user_tz.localize(dt)
            update_data["appointment_date"] = localized_date.astimezone(pytz.UTC)
            
            # Quitamos la región para que no intente guardarla en la tabla si no existe esa columna
            update_data.pop("timezone_region", None)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"invalid_timezone_or_date: {str(e)}")

    # 5. Aplicar cambios solo de los campos filtrados
    for key, value in update_data.items():
        if hasattr(appointment, key): # Verificación extra de seguridad
            setattr(appointment, key, value)

    try:
        # 6. Auditoría con datos limpios
        audit_payload = jsonable_encoder({
            "appointment_id": appointment_id,
            "changes": update_data 
        })

        register_action_log(
            db=db,
            establishment_id=uid,
            action="UPDATE_APPOINTMENT",
            method="PATCH",
            path=request.url.path,
            payload=audit_payload,
            request=request
        )

        db.commit()
        db.refresh(appointment)

        return {
            "status": "success", 
            "id": appointment.id,
            "updated_fields": list(update_data.keys())
        }

    except Exception as e:
        db.rollback()
        print(f"🚨 UPDATE APPOINTMENT ERROR: {str(e)}")
        raise HTTPException(status_code=500, detail="internal_update_error")


@router.delete("/{appointment_id}", status_code=status.HTTP_200_OK)
def delete_appointment(
    appointment_id: int,
    db: Session = Depends(get_db),
    token_data: dict = Depends(verify_firebase_token)
):
    # 1. Extraer el UID del establecimiento desde el token de Firebase
    establishment_id = token_data.get('uid')

    # 2. Buscar el registro asegurando que pertenezca al establecimiento
    appointment = db.query(Appointment).filter(
        Appointment.id == appointment_id,
        Appointment.establishment_id == establishment_id
    ).first()

    # 3. Si no existe o es de otro local, lanzamos 404 por seguridad
    if not appointment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="APPOINTMENT_NOT_FOUND"
        )

    # 4. REGLA DE NEGOCIO: Bloquear si ya se envió el mensaje
    # Validamos si whatsapp_id tiene contenido (no es None y no es string vacío)
    if appointment.whatsapp_id and str(appointment.whatsapp_id).strip():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="RECORD_IMMUTABLE_MESSAGE_ALREADY_SENT"
        )

    # 5. Intentar la eliminación física del row
    try:
        db.delete(appointment)
        db.commit()
        
        return {
            "status": "success",
            "message": f"Appointment {appointment_id} deleted successfully.",
            "id_deleted": appointment_id
        }

    except Exception as e:
        db.rollback()
        # Log del error interno para debug
        print(f"🚨 ERROR SQL DELETE: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="DATABASE_ERROR_ON_DELETE"
        )