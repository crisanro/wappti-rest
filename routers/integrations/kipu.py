from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_, func
from datetime import datetime, timezone, timedelta, time
import pytz
import httpx
from typing import Optional, List
import traceback
import os
from core.database import get_db
from core.auth import verify_firebase_token
from core.utils import register_action_log, decrypt_value, register_action_log

# Import English models
from models import *

# Import updated schemas
from schemas.operations import (
    CustomerHistoryCreate, 
    AppointmentCreate, 
    UsageAuditLogCreate, AppointmentUpdate
)
from schemas.users import TagResponse
from schemas.kipu import BillingProfileCreate

KIPU_BASE_URL = os.getenv("KIPU_BASE_URL")

router = APIRouter(dependencies=[Depends(verify_firebase_token)])


def validate_ecuadorian_id(number: str) -> bool:
    if not number.isdigit() or len(number) != 10:
        return False
    
    provincia = int(number[0:2])
    if not (0 < provincia <= 24 or provincia == 30):
        return False
    
    tercer_digito = int(number[2])
    if tercer_digito >= 6: # Es RUC de sociedad o público, no CI natural
        return False

    # Algoritmo de validación del décimo dígito
    coeficientes = [2, 1, 2, 1, 2, 1, 2, 1, 2]
    suma = 0
    for i in range(9):
        valor = int(number[i]) * coeficientes[i]
        suma += valor if valor < 10 else valor - 9
    
    verificador = int(number[9])
    digito_esperado = (10 - (suma % 10)) % 10
    return verificador == digito_esperado

# Función auxiliar para obtener y descifrar el token de un establecimiento
async def get_kipu_token(db: Session, establishment_id: str):
    token_record = db.query(EstablishmentToken).filter(
        EstablishmentToken.establishment_id == establishment_id,
        EstablishmentToken.provider == "kipu"
    ).first()
    
    if not token_record:
        raise HTTPException(status_code=404, detail="KIPU_INTEGRATION_NOT_FOUND")
    
    return decrypt_value(token_record.encrypted_token)


@router.post("/billing-profiles", status_code=201)
def create_billing_profile(
    data: BillingProfileCreate, 
    db: Session = Depends(get_db),
    token_data: dict = Depends(verify_firebase_token) 
):
    try:
        establishment_id = token_data.get('uid')
        id_num = data.tax_id_number.strip()
        
        # 1. Mapeo de códigos CORREGIDO (4: RUC, 5: Cédula, 6: Pasaporte, 8: Exterior)
        # Según estándar SRI Ecuador
        type_mapping = {"RUC": "04", "Cédula": "05", "Pasaporte": "06", "Exterior": "08"}
        tax_code = type_mapping.get(data.tax_id_type)
        
        if not tax_code:
            raise HTTPException(status_code=400, detail="INVALID_TAX_ID_TYPE")

        # 2. VALIDACIÓN DE DUPLICADOS POR CLIENTE
        already_exists = db.query(CustomerBillingProfile).filter(
            CustomerBillingProfile.tax_id_number == id_num,
            CustomerBillingProfile.customer_id == data.customer_id
        ).first()

        if already_exists:
            raise HTTPException(
                status_code=400, 
                detail="TAX_ID_ALREADY_EXISTS_FOR_THIS_CUSTOMER"
            )

        # 3. SEGURIDAD: Validar que el cliente pertenece al establecimiento
        customer_exists = db.query(Customer).filter(
            Customer.id == data.customer_id,
            Customer.establishment_id == establishment_id
        ).first()

        if not customer_exists:
            raise HTTPException(status_code=403, detail="CUSTOMER_NOT_OWNED")

        # 4. VALIDACIONES DE ALGORITMO (Ecuador)
        # Código 05 es Cédula
        if tax_code == "05":
            if not validate_ecuadorian_id(id_num):
                raise HTTPException(status_code=400, detail="INVALID_CEDULA_DIGIT_VERIFIER")
        
        # Código 04 es RUC
        elif tax_code == "04":
            if len(id_num) != 13 or not id_num.endswith("001"):
                raise HTTPException(status_code=400, detail="INVALID_RUC_FORMAT")
            # Validación para RUC de personas naturales (tercer dígito menor a 6)
            if int(id_num[2]) < 6 and not validate_ecuadorian_id(id_num[0:10]):
                raise HTTPException(status_code=400, detail="INVALID_RUC_NATURAL_PERSON")

        # 5. GUARDADO
        new_profile = CustomerBillingProfile(
            customer_id=data.customer_id,
            establishment_id=establishment_id,
            tax_id_type=tax_code,
            tax_id_number=id_num,
            business_name=data.business_name.upper()
        )

        db.add(new_profile)
        db.commit()
        db.refresh(new_profile)

        return {"status": "success", "id": new_profile.id}

    except HTTPException as he:
        raise he
    except Exception as e:
        db.rollback()
        # Asegúrate de importar traceback si vas a usarlo
        import traceback
        print(f"🚨 ERROR: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail="INTERNAL_SERVER_ERROR_BILLING")
    

@router.get("/status")
async def get_kipu_status(
    db: Session = Depends(get_db),
    token_data: dict = Depends(verify_firebase_token)
):
    est_id = token_data.get('uid')
    token = await get_kipu_token(db, est_id)

    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{KIPU_BASE_URL}/integrations/status",
            headers={"x-api-key": token}
        )
    
    return response.json()


@router.post("/validate-point")
async def validate_kipu_point(
    data: dict, # Recibe {"estab_codigo": "...", "punto_codigo": "..."}
    db: Session = Depends(get_db),
    token_data: dict = Depends(verify_firebase_token)
):
    est_id = token_data.get('uid')
    token = await get_kipu_token(db, est_id)

    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{KIPU_BASE_URL}/integrations/validate",
            headers={"x-api-key": token},
            json=data
        )
    
    return response.json()


@router.post("/facturar")
async def send_kipu_invoice(
    payload: dict, # El JSON completo que mencionaste
    request: Request,
    db: Session = Depends(get_db),
    token_data: dict = Depends(verify_firebase_token)
):
    est_id = token_data.get('uid')
    token = await get_kipu_token(db, est_id)

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                f"{KIPU_BASE_URL}/integrations/invoice",
                headers={"x-api-key": token},
                json=payload,
                timeout=30.0 # Las APIs de facturación pueden ser lentas
            )
            
            # Registramos el intento en la auditoría de Wappti
            register_action_log(
                db, 
                establishment_id=est_id, 
                action="KIPU_INVOICE_SENT", 
                method=request.method, 
                path=request.url.path, 
                request=request,
                status_code=response.status_code
            )

            return response.json()

        except httpx.RequestError as exc:
            print(f"An error occurred while requesting {exc.request.url!r}.")
            raise HTTPException(status_code=503, detail="KIPU_SERVICE_UNAVAILABLE")