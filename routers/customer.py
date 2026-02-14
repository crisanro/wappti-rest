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

# --- 2. SEARCH BY NAME OR SURNAME ---
@router.get("/search")
def search_customers(
    query: str, 
    db: Session = Depends(get_db), 
    token_data: dict = Depends(verify_firebase_token)
):
    establishment_id = token_data.get('uid')
    search_term = f"%{query}%"
    results = db.query(Customer).with_entities(
        Customer.id,
        Customer.first_name,
        Customer.last_name,
        Customer.last_visit
    ).filter(
        and_(
            Customer.establishment_id == establishment_id,
            or_(
                Customer.first_name.ilike(search_term),
                Customer.last_name.ilike(search_term)
            )
        )
    ).order_by(Customer.last_name.asc()).all()
    return [{"id": r.id, "first_name": r.first_name, "last_name": r.last_name, "last_visit": r.last_visit} for r in results]


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
    


# --- 3. CREATE NEW CUSTOMER ---
@router.post("/", status_code=status.HTTP_201_CREATED)
def create_customer(
    data: CustomerCreate, # CORREGIDO: Antes UserCreate
    request: Request,
    db: Session = Depends(get_db), 
    token_data: dict = Depends(verify_firebase_token)
):
    establishment_id = token_data.get('uid')
    
    def purify_text(value):
        if value is None: return ""
        return " ".join(str(value).split()).title()

    try:
        clean_first_name = purify_text(data.first_name)
        clean_last_name = purify_text(data.last_name)

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

        register_action_log(
            db, 
            establishment_id=establishment_id, 
            action="CUSTOMER_CREATE",
            method="POST",
            path="/customers/create",
            payload={"new_id": new_customer.id, "name": f"{clean_first_name} {clean_last_name}"},
            request=request
        )
        return {"status": "success", "id": new_customer.id, "full_name": f"{clean_first_name} {clean_last_name}"}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Internal error: {str(e)}")
    


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


# --- 6. TAG MANAGEMENT (TOGGLE) ---
@router.get("/{customer_id}/tags", response_model=List[TagResponse])
def get_customer_tags(
    customer_id: int,
    db: Session = Depends(get_db),
    token_data: dict = Depends(verify_firebase_token)
):
    try:
        establishment_id = token_data.get('uid')

        # 1. Obtener al cliente y su columna tag_ids (ARRAY)
        customer = db.query(Customer).filter(
            Customer.id == customer_id,
            Customer.establishment_id == establishment_id
        ).first()

        if not customer:
            raise HTTPException(status_code=404, detail="customer_not_found")

        # Si no tiene tags, devolvemos lista vacía
        if not customer.tag_ids:
            return []

        # 2. Consultar los nombres en la tabla customer_tags
        # Usamos el nombre del MODELO: CustomerTag
        tags = db.query(CustomerTag).filter(
            CustomerTag.id.in_(customer.tag_ids),
            CustomerTag.establishment_id == establishment_id
        ).all()

        return tags

    except Exception as e:
        print(f"🚨 Error: {str(e)}")
        raise HTTPException(
            status_code=500, 
            detail="customer_tags_fetch_error"
        )
    


@router.patch("/{customer_id}", response_model=CustomerListResponse)
def update_customer_info(
    customer_id: int,
    data: CustomerUpdate,
    request: Request, # <--- Agregado para el log de IP
    db: Session = Depends(get_db),
    token_data: dict = Depends(verify_firebase_token)
):
    """
    Updates customer fields including country information.
    """
    uid = token_data.get('uid')
    
    # 1. Buscar al cliente
    customer = db.query(Customer).filter(
        Customer.id == customer_id, 
        Customer.establishment_id == uid
    ).first()

    if not customer:
        raise HTTPException(status_code=404, detail="customer_not_found")

    # 2. Extraer solo los campos que vienen en el JSON (incluyendo country_code/name)
    # Ignoramos tag_ids aquí porque eso se maneja en otro endpoint específico (/tags)
    update_data = data.model_dump(exclude_unset=True, exclude={"tag_ids"})

    # 3. Aplicar los cambios dinámicamente
    for key, value in update_data.items():
        setattr(customer, key, value)

    try:
        # 4. Registrar Auditoría (Antes del commit para asegurar atomicidad)
        register_action_log(
            db=db, 
            establishment_id=uid, 
            action="UPDATE_CUSTOMER_INFO", 
            method="PATCH", 
            path=request.url.path, 
            payload=update_data, # Solo guardamos lo que realmente cambió
            request=request
        )

        db.commit()
        db.refresh(customer)
        
        return customer

    except Exception as e:
        db.rollback()
        print(f"🚨 UPDATE ERROR: {str(e)}")
        raise HTTPException(status_code=500, detail="internal_update_error")
    

@router.patch("/{customer_id}/tags")
def toggle_customer_tag(
    customer_id: int, 
    data: TagUpdateSchema, 
    request: Request, # Asegúrate de que esté aquí
    db: Session = Depends(get_db), 
    token_data: dict = Depends(verify_firebase_token)
):
    establishment_id = token_data.get('uid')
    
    customer = db.query(Customer).filter(and_(Customer.id == customer_id, Customer.establishment_id == establishment_id)).first()
    tag = db.query(CustomerTag).filter(and_(CustomerTag.id == data.tag_id, CustomerTag.establishment_id == establishment_id)).first()

    if not customer or not tag:
        raise HTTPException(status_code=404, detail="Customer or Tag not found")

    current_tags = list(customer.tag_ids) if customer.tag_ids else []
    changed = False
    action_type = "ADD" if data.action == 1 else "REMOVE"

    if data.action == 1: # ADD
        if data.tag_id not in current_tags:
            current_tags.append(data.tag_id)
            tag.total_customers = (tag.total_customers or 0) + 1
            changed = True
    elif data.action == 0: # REMOVE
        if data.tag_id in current_tags:
            current_tags.remove(data.tag_id)
            tag.total_customers = max(0, (tag.total_customers or 1) - 1)
            changed = True
    
    if changed:
        customer.tag_ids = current_tags
        
        # Log más descriptivo
        register_action_log(
            db, 
            establishment_id=establishment_id, 
            action="TAG_TOGGLE", 
            method="PATCH", 
            path=request.url.path, 
            payload={
                "customer_id": customer_id, 
                "tag_id": data.tag_id, 
                "tag_name": tag.name, 
                "action": action_type
            }, 
            request=request
        )
        
        db.commit()
    
    return {"status": "success", "updated_tags": customer.tag_ids}


# --- 8. DELETE CUSTOMER ---
@router.delete("/{customer_id}")
def delete_customer(
    customer_id: int, 
    request: Request, # Necesario para el log
    db: Session = Depends(get_db),
    token_data: dict = Depends(verify_firebase_token)
):
    establishment_id = token_data.get('uid')
    
    customer = db.query(Customer).filter(
        and_(Customer.id == customer_id, Customer.establishment_id == establishment_id)
    ).first()
    
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    
    # Guardamos info para el log antes de borrar
    customer_name = f"{customer.first_name} {customer.last_name}"
    
    try:
        db.delete(customer)
        
        # Registramos la acción
        register_action_log(
            db, 
            establishment_id=establishment_id, 
            action="CUSTOMER_DELETE",
            method="DELETE",
            path=f"/customers/{customer_id}",
            payload={"deleted_id": customer_id, "customer_name": customer_name},
            request=request
        )
        
        db.commit()
        return {"message": "Customer deleted successfully"}
        
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Error deleting customer: {str(e)}")


@router.post("/customer-plans", status_code=201)
def create_customer_planning(
    data: CustomerPlanCreate, 
    db: Session = Depends(get_db),
    token_data: dict = Depends(verify_firebase_token)
):
    establishment_id = token_data.get('uid')

    try:
        # 1. SEGURIDAD: Validar cliente
        customer = db.query(Customer).filter(
            Customer.id == data.customer_id,
            Customer.establishment_id == establishment_id
        ).first()

        if not customer:
            raise HTTPException(status_code=403, detail="CUSTOMER_NOT_OWNED")

        # 2. CREAR CABECERA (Incluyendo las notas generales)
        new_plan = CustomerPlan(
            customer_id=data.customer_id,
            establishment_id=establishment_id,
            title=data.title,
            general_notes=data.general_notes, 
        )
        db.add(new_plan)
        db.flush() 

        # 3. CREAR DETALLES
        for item in data.items:
            new_item = CustomerPlanItem(
                plan_id=new_plan.id,
                description=item.description,
                amount=item.amount
            )
            db.add(new_item)

        db.commit()
        return {"status": "success", "plan_id": new_plan.id}

    except Exception as e:
        db.rollback()
        print(f"🚨 ERROR: {str(e)}")
        raise HTTPException(status_code=500, detail="INTERNAL_SERVER_ERROR_PLANNING")


@router.get("/customer-plans/{customer_id}")
def get_all_customer_plans(
    customer_id: int,
    tz_name: str = "America/Guayaquil", # Recibimos la zona horaria del frontend
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

        # 2. Traer todos los planes del cliente
        plans = db.query(CustomerPlan).filter(
            CustomerPlan.customer_id == customer_id,
            CustomerPlan.establishment_id == establishment_id
        ).order_by(CustomerPlan.created_at.desc()).all()

        # 3. Función auxiliar para formatear fechas a la zona local
        def format_local(dt):
            if not dt: return None
            # Si el objeto de la DB no tiene zona horaria (naive), le asignamos UTC
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=pytz.UTC)
            # Convertimos al huso horario solicitado y pasamos a ISO string
            return dt.astimezone(local_tz).isoformat()

        # 4. Construir la respuesta
        result = []
        for plan in plans:
            # Calculamos el total de este plan
            plan_total = sum(item.amount for item in plan.items)
            
            result.append({
                "plan_id": plan.id,
                "title": plan.title,
                "general_notes": plan.general_notes,
                # Fecha de creación del PLAN formateada
                "created_at": format_local(plan.created_at),
                "total_value": float(plan_total),
                "items": [
                    {
                        "id": item.id,
                        "description": item.description,
                        "amount": float(item.amount),
                        "is_completed": item.is_completed,
                        # Fecha de creación de cada ITEM formateada
                        "created_at": format_local(item.created_at)
                    } for item in plan.items
                ]
            })

        return result

    except Exception as e:
        import traceback
        print(f"🚨 ERROR GET ALL PLANS: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail="INTERNAL_SERVER_ERROR_LISTING_PLANS")
    



@router.post("/debts", status_code=201)
def create_debt(
    data: DebtCreate, 
    db: Session = Depends(get_db),
    token_data: dict = Depends(verify_firebase_token)
):
    establishment_id = token_data.get('uid')
    
    # Validar que el cliente pertenece al establecimiento
    customer = db.query(Customer).filter(
        Customer.id == data.customer_id,
        Customer.establishment_id == establishment_id
    ).first()

    if not customer:
        raise HTTPException(status_code=403, detail="CUSTOMER_NOT_OWNED")

    new_debt = CustomerDebt(
        customer_id=data.customer_id,
        establishment_id=establishment_id,
        title=data.title,
        total_amount=data.total_amount,
        notes=data.notes
    )
    
    db.add(new_debt)
    db.commit()
    db.refresh(new_debt)
    
    return {"status": "success", "debt_id": new_debt.id}


@router.post("/payments", status_code=201)
def create_payment(
    data: PaymentCreate, 
    db: Session = Depends(get_db),
    token_data: dict = Depends(verify_firebase_token)
):
    establishment_id = token_data.get('uid')

    # 1. Validar que la deuda existe y pertenece a este establecimiento
    debt = db.query(CustomerDebt).filter(
        CustomerDebt.id == data.debt_id,
        CustomerDebt.establishment_id == establishment_id
    ).first()

    if not debt:
        raise HTTPException(status_code=404, detail="DEBT_NOT_FOUND_OR_ACCESS_DENIED")

    # 2. Registrar el abono
    new_payment = CustomerPayment(
        debt_id=data.debt_id,
        amount=data.amount,
        payment_method=data.payment_method,
        notes=data.notes
    )
    
    db.add(new_payment)
    db.commit()
    db.refresh(new_payment)

    return {
        "status": "success", 
        "payment_id": new_payment.id,
        "message": "Payment registered successfully"
    }


@router.get("/debts/{customer_id}")
def get_customer_financial_summary(
    customer_id: int,
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

        debts = db.query(CustomerDebt).options(
            joinedload(CustomerDebt.payments)
        ).filter(
            CustomerDebt.customer_id == customer_id,
            CustomerDebt.establishment_id == establishment_id
        ).order_by(CustomerDebt.created_at.desc()).all()

        all_debts_data = []
        grand_total_debt = 0.0
        grand_total_paid = 0.0

        for d in debts:
            total_paid_in_debt = sum(float(p.amount) for p in d.payments)
            total_debt_amount = float(d.total_amount)
            
            # --- CÁLCULO DEL PORCENTAJE (0.0 a 1.0) ---
            # Evitamos división por cero si la deuda se creó con 0 por error
            payment_ratio = 0.0
            if total_debt_amount > 0:
                payment_ratio = total_paid_in_debt / total_debt_amount
                # Limitamos a 1.0 por si el cliente pagó de más (excedente)
                payment_ratio = min(1.0, payment_ratio)

            current_balance = total_debt_amount - total_paid_in_debt
            grand_total_debt += total_debt_amount
            grand_total_paid += total_paid_in_debt

            all_debts_data.append({
                "debt_id": d.id,
                "title": d.title,
                "total_amount": total_debt_amount,
                "total_paid": total_paid_in_debt,
                "payment_percentage": round(payment_ratio, 2), # Ejemplo: 0.45
                "balance": current_balance,
                "created_at": d.created_at.astimezone(local_tz).isoformat() if d.created_at else None,
                "payments": [
                    {
                        "payment_id": p.id,
                        "amount": float(p.amount),
                        "method": p.payment_method,
                        "created_at": p.created_at.astimezone(local_tz).isoformat() if p.created_at else None
                    } for p in d.payments
                ]
            })

        return {
            "customer_id": customer_id,
            "summary": {
                "total_debt_all_time": grand_total_debt,
                "total_paid_all_time": grand_total_paid,
                "current_outstanding_balance": grand_total_debt - grand_total_paid,
                # Porcentaje global de cobro de este cliente
                "global_payment_percentage": round(grand_total_paid / grand_total_debt, 2) if grand_total_debt > 0 else 0.0
            },
            "debts": all_debts_data
        }

    except Exception as e:
        import traceback
        print(f"🚨 ERROR FINANCIAL GET: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail="INTERNAL_SERVER_ERROR_FINANCIALS")