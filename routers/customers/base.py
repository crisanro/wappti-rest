from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import and_, or_, any_, asc, func
from datetime import datetime, timezone, timedelta
from typing import Optional, List
import traceback
import pytz
from core.database import get_db
from core.auth import verify_firebase_token # Asegúrate que el nombre coincida con core/auth.py
from core.utils import register_action_log

# Importación de Modelos (Ubicaciones correctas)
from models import *

# Importación de Schemas (Usando los nombres de tu archivo schemas/users.py)
from schemas.users import CustomerCreate, CustomerUpdate,TagUpdateSchema, TagBase, TagResponse, CustomerListResponse, CustomerListResponse
from schemas.operations import CustomerPlanCreate
from schemas.financials import DebtCreate, PaymentCreate

router = APIRouter(dependencies=[Depends(verify_firebase_token)])


# --- 1. FIND DUPLICATES ---
@router.get("/find-duplicates")
def find_duplicate_customers(
    country_code: int, 
    phone: int, 
    db: Session = Depends(get_db), 
    token_data: dict = Depends(verify_firebase_token)
):
    try:
        establishment_id = token_data.get('uid')
        results = db.query(Customer).with_entities(
            Customer.first_name, 
            Customer.last_name
        ).filter(
            and_(
                Customer.establishment_id == establishment_id,
                Customer.country_code == country_code,
                Customer.phone == phone
            )
        ).all()
        return [{"first_name": r.first_name, "last_name": r.last_name} for r in results]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Server failure: {str(e)}")
   

# --- 1. LIST ALL CUSTOMERS ---
@router.get("/", response_model=List[CustomerListResponse])
def list_establishment_customers(
    db: Session = Depends(get_db), 
    token_data: dict = Depends(verify_firebase_token)
):
    try:
        establishment_id = token_data.get('uid')

        # Subquery para obtener la fecha de la última visita
        last_visit_subquery = db.query(
            CustomerHistory.customer_id,
            func.max(CustomerHistory.created_at).label("max_date")
        ).filter(CustomerHistory.establishment_id == establishment_id)\
         .group_by(CustomerHistory.customer_id).subquery()

        # Consulta principal
        query_results = db.query(
            Customer, 
            last_visit_subquery.c.max_date
        ).outerjoin(
            last_visit_subquery, 
            Customer.id == last_visit_subquery.c.customer_id
        ).filter(
            Customer.establishment_id == establishment_id
        ).order_by(asc(Customer.last_name)).all()

        formatted_list = []
        for customer_obj, max_date in query_results:
            # Solo pasamos los datos básicos y la fecha de referencia
            formatted_list.append({
                "id": customer_obj.id,
                "first_name": customer_obj.first_name,
                "last_name": customer_obj.last_name,
                "phone": customer_obj.phone,
                "country_code": customer_obj.country_code,
                "last_visit_date": max_date # Se usa para el cálculo interno del schema
            })

        return formatted_list

    except Exception as e:
        print(f"Error: {e}")
        raise HTTPException(status_code=500, detail="Error al obtener la lista de clientes")
    
@router.post("/", status_code=status.HTTP_201_CREATED)
def create_customer(
    data: CustomerCreate,
    request: Request,
    db: Session = Depends(get_db), 
    token_data: dict = Depends(verify_firebase_token)
):
    """
    Creates a new customer associated with the authenticated establishment.
    Includes 'language' preference for personalized notifications.
    """
    establishment_id = token_data.get('uid')
    
    def purify_text(value):
        if value is None: return ""
        return " ".join(str(value).split()).title()

    try:
        clean_first_name = purify_text(data.first_name)
        clean_last_name = purify_text(data.last_name)

        # Create new customer instance
        # language is automatically included via **data.model_dump()
        new_customer = Customer(
            **data.model_dump(exclude={"first_name", "last_name"}),
            first_name=clean_first_name,
            last_name=clean_last_name,
            establishment_id=establishment_id,
            created_at=datetime.now(timezone.utc)
        )
        
        db.add(new_customer)
        db.commit()
        db.refresh(new_customer)

        # Audit Log
        register_action_log(
            db=db, 
            establishment_id=establishment_id, 
            action="CUSTOMER_CREATE",
            method="POST",
            path=request.url.path,
            payload={
                "customer_id": new_customer.id, 
                "name": f"{clean_first_name} {clean_last_name}",
                "language": new_customer.language
            },
            request=request
        )

        return {
            "status": "success", 
            "id": new_customer.id, 
            "full_name": f"{clean_first_name} {clean_last_name}",
            "language": new_customer.language
        }

    except Exception as e:
        db.rollback()
        print(f"🚨 CUSTOMER CREATE ERROR: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
            detail="Internal server error while creating customer."
        )
    

@router.get("/{customer_id}")
def get_customer_detail(
    customer_id: int, 
    tz_name: str = "America/Guayaquil",
    db: Session = Depends(get_db),
    token_data: dict = Depends(verify_firebase_token)
):
    try:
        establishment_id = token_data.get('uid')

        # 1. Configurar Zona Horaria Local
        try:
            local_tz = pytz.timezone(tz_name)
        except Exception:
            local_tz = pytz.UTC

        # 2. Buscar al cliente (Validando pertenencia al establecimiento)
        customer = db.query(Customer).filter(
            Customer.id == customer_id,
            Customer.establishment_id == establishment_id
        ).first()

        if not customer:
            raise HTTPException(status_code=404, detail="customer_not_found")

        # 3. Buscar la PRÓXIMA cita
        now_utc = datetime.now(timezone.utc)
        next_appo = db.query(Appointment).filter(
            and_(
                Appointment.customer_id == customer_id,
                Appointment.appointment_date >= now_utc
            )
        ).order_by(Appointment.appointment_date.asc()).first()

        # 4. BUSCAR PERFILES DE FACTURACIÓN (Nueva sección)
        billing_db = db.query(CustomerBillingProfile).filter(
            CustomerBillingProfile.customer_id == customer_id,
            CustomerBillingProfile.establishment_id == establishment_id
        ).all()

        # 5. Función para formatear fechas
        def format_local(dt):
            if not dt: return None
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(local_tz).isoformat()

        # 6. Respuesta Final Combinada
        return {
            "id": customer.id,
            "first_name": customer.first_name,
            "last_name": customer.last_name,
            "full_name": f"{customer.first_name} {customer.last_name}".strip(),
            "phone": customer.phone,
            "country_code": customer.country_code,
            "country_name": customer.country_name,
            "email": customer.email,
            "identification_id": customer.identification_id,
            "notes": customer.notes,
            "tag_ids": customer.tag_ids if customer.tag_ids else [],
            "created_at": format_local(customer.created_at),
            "last_visit": format_local(customer.last_visit),
            "next_appointment_date": format_local(next_appo.appointment_date) if next_appo else None,
            "next_appointment_reason": next_appo.reason if next_appo else None,
            "has_next_appointment": next_appo is not None,
            "language":customer.language,
            
            # LISTA DE PERFILES DE FACTURACIÓN
            "billing_profiles": [
                {
                    "id": b.id,
                    "tax_id_type": b.tax_id_type,
                    "tax_id_number": b.tax_id_number,
                    "business_name": b.business_name
                } for b in billing_db
            ]
        }

    except Exception as e:
        import traceback
        print(f"🚨 ERROR EN DETALLE CLIENTE: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail="customer_detail_error")


@router.get("/activity/{customer_id}")
def get_customer_activity_summary(
    customer_id: int, 
    db: Session = Depends(get_db),
    token_data: dict = Depends(verify_firebase_token)
):
    try:
        establishment_id = token_data.get('uid')
        now_utc = datetime.now(timezone.utc)
        now_unix = int(now_utc.timestamp())

        # 1. Obtener último registro de historia
        last_record = db.query(CustomerHistory).filter(
            CustomerHistory.customer_id == customer_id,
            CustomerHistory.establishment_id == establishment_id
        ).order_by(CustomerHistory.created_at.desc()).first()

        # 2. Obtener próxima cita
        next_appo = db.query(Appointment).filter(
            Appointment.customer_id == customer_id,
            Appointment.establishment_id == establishment_id,
            Appointment.appointment_date >= now_utc
        ).order_by(Appointment.appointment_date.asc()).first()

        def get_time_data(db_date):
            if not db_date: 
                return None
            
            # Normalizar a UTC si la DB no trae zona horaria
            if db_date.tzinfo is None:
                db_date = db_date.replace(tzinfo=timezone.utc)
            
            ts = int(db_date.timestamp())
            # Calculamos la diferencia absoluta en horas
            diff_hours = abs((db_date - now_utc).total_seconds()) / 3600
            
            return {
                "timestamp": ts,
                "hours_diff": round(diff_hours, 2)
            }

        return {
            "current_server_time_unix": now_unix,
            "last_visit": {
                "status": "success" if last_record else "no_history",
                "data": get_time_data(last_record.created_at) if last_record else None
            },
            "next_appointment": {
                "status": "success" if next_appo else "no_upcoming_appointments",
                "data": get_time_data(next_appo.appointment_date) if next_appo else None
            }
        }

    except Exception as e:
        print(f"DEBUG_SYSTEM_ERROR: {str(e)}") 
        raise HTTPException(
            status_code=500, 
            detail="activity_summary_processing_error"
        )


# --- 8. DELETE CUSTOMER ---
@router.delete("/{customer_id}")
def delete_customer_data(
    customer_id: int,
    db: Session = Depends(get_db),
    token_data: dict = Depends(verify_firebase_token)
):
    establishment_id = token_data.get('uid')

    # 1. Fetch the customer and verify ownership
    customer = db.query(Customer).filter(
        Customer.id == customer_id, 
        Customer.establishment_id == establishment_id
    ).first()

    if not customer:
        raise HTTPException(status_code=404, detail="CUSTOMER_NOT_FOUND")

    # 2. Check for sent messages (sent records must be preserved)
    sent_appointments = db.query(Appointment).filter(
        Appointment.customer_id == customer_id,
        Appointment.whatsapp_id.isnot(None),
        Appointment.whatsapp_id != ""
    ).all()

    try:
        if not sent_appointments:
            # CASE A: No sent history. Perform a full physical delete.
            db.delete(customer)
            db.commit()
            return {"status": "full_delete", "message": "all_records_permanently_removed"}
        
        else:
            # CASE B: Sent history exists. Anonymize the customer and clean up.
            
            # 1. Delete financial "trash" (plans, debts, items)
            db.query(CustomerPlan).filter(CustomerPlan.customer_id == customer_id).delete()
            db.query(CustomerDebt).filter(CustomerDebt.customer_id == customer_id).delete()
            
            # 2. Delete appointments that were NEVER sent
            db.query(Appointment).filter(
                Appointment.customer_id == customer_id,
                (Appointment.whatsapp_id == None) | (Appointment.whatsapp_id == "")
            ).delete()

            # 3. Anonymize sensitive fields in the Customer table
            customer.first_name = "deleted_user"
            customer.last_name = "deleted_user"
            customer.email = f"deleted_{customer_id}@deleted.com"
            customer.phone = "0987654321"
            customer.notes = "anonymized_due_to_whatsapp_history_retention"
            
            # If you have extra fields like address or identification_number, reset them here:
            # customer.address = None
            # customer.tax_id = None

            db.commit()
            return {
                "status": "anonymized", 
                "message": "financial_records_deleted_and_user_anonymized"
            }

    except Exception as e:
        db.rollback()
        print(f"🚨 DELETE_PROCESS_ERROR: {e}")
        raise HTTPException(status_code=500, detail="INTERNAL_SERVER_ERROR_ON_DELETE")