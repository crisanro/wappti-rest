from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy.orm import Session, joinedload
import pytz
from core.database import get_db
from core.auth import verify_firebase_token # Asegúrate que el nombre coincida con core/auth.py
from core.utils import register_action_log

# Importación de Modelos (Ubicaciones correctas)
from models import *

# Importación de Schemas (Usando los nombres de tu archivo schemas/users.py)
from schemas.operations import CustomerPlanCreate
from schemas.financials import DebtCreate, PaymentCreate
router = APIRouter(dependencies=[Depends(verify_firebase_token)])


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