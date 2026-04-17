from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_, func
from datetime import datetime, timezone, timedelta, time
import pytz
import json
import httpx
from typing import Optional, List
import traceback
from core.config import settings
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

KIPU_BASE_URL = settings.KIPU_BASE_URL

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


@router.get("/status")
async def get_kipu_status(
    db: Session = Depends(get_db),
    token_data: dict = Depends(verify_firebase_token)
):
    est_id = token_data.get('uid')
    token = await get_kipu_token(db, est_id)

    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(
                f"{KIPU_BASE_URL}/api/v1/public/integraciones/status",
                headers={"x-api-key": token},
                timeout=10.0
            )
            
            # Si Kipu da 500, esto lanzará una excepción controlada
            response.raise_for_status() 
            
            return response.json()

        except httpx.HTTPStatusError as e:
            # Capturamos el error 500 de Kipu aquí
            print(f"❌ Kipu devolvió error {e.response.status_code}: {e.response.text}")
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY, 
                detail="Kipu integration is currently unavailable (500)"
            )
        except Exception as e:
            # Cualquier otro error (timeout, red, etc)
            raise HTTPException(status_code=500, detail="Unexpected integration error")
        

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
            f"{KIPU_BASE_URL}/api/v1/public/integraciones/validate",
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
                f"{KIPU_BASE_URL}/api/v1/public/integraciones/invoice",
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


@router.post("/clientes/buscar")
async def search_kipu_clients(
    data: dict, # Recibe {"terminos": ["uid1", "uid2"]}
    db: Session = Depends(get_db),
    token_data: dict = Depends(verify_firebase_token)
):
    est_id = token_data.get('uid')
    token = await get_kipu_token(db, est_id)

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                f"{KIPU_BASE_URL}/api/v1/public/clientes/buscar",
                headers={"x-api-key": token},
                json=data,
                timeout=15.0
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            print(f"❌ Error en búsqueda: {e.response.text}")
            raise HTTPException(status_code=e.response.status_code, detail="CLIENT_SEARCH_ERROR")

@router.post("/clientes/crear")
async def create_kipu_client(
    payload: dict, # Datos: tipo_identificacion_sri, identificacion, razon_social, etc.
    db: Session = Depends(get_db),
    token_data: dict = Depends(verify_firebase_token)
):
    est_id = token_data.get('uid')
    token = await get_kipu_token(db, est_id)

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                f"{KIPU_BASE_URL}/api/v1/public/clientes/",
                headers={"x-api-key": token},
                json=payload,
                timeout=15.0
            )
            
            # Si Kipu devuelve 400 es probable que el cliente ya exista
            if response.status_code == 400:
                return response.json() # Devolvemos el error de Kipu directamente
                
            response.raise_for_status()
            return response.json()
            
        except httpx.HTTPStatusError as e:
            print(f"❌ Error al crear cliente: {e.response.text}")
            raise HTTPException(status_code=e.response.status_code, detail="CLIENT_CREATION_FAILED")
        
        
@router.get("/clientes/validar/{cliente_uid}")
async def validate_kipu_client(
    cliente_uid: str,
    db: Session = Depends(get_db),
    token_data: dict = Depends(verify_firebase_token)
):
    est_id = token_data.get('uid')
    token = await get_kipu_token(db, est_id)

    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(
                f"{KIPU_BASE_URL}/api/v1/public/clientes/validar-cliente/{cliente_uid}",
                headers={"x-api-key": token},
                timeout=10.0
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            raise HTTPException(status_code=e.response.status_code, detail="CLIENT_VALIDATION_ERROR")
        

@router.post("/{customer_id}/billing-profiles/add")
async def add_customer_billing_uid(
    customer_id: int,
    uid: str, 
    db: Session = Depends(get_db),
    token_data: dict = Depends(verify_firebase_token)
):
    establishment_id = token_data.get('uid')
    
    # 1. LIMPIEZA: Quitamos espacios, comas o saltos de línea accidentales
    clean_uid = uid.strip().replace(",", "") 

    customer = db.query(Customer).filter(
        Customer.id == customer_id,
        Customer.establishment_id == establishment_id
    ).first()

    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    try:
        # Aseguramos que la lista exista
        if customer.billing_profile_uids is None:
            customer.billing_profile_uids = []
        
        # 2. VALIDACIÓN: Ahora sí comparamos el ID limpio
        if clean_uid not in customer.billing_profile_uids:
            # Usamos list() para que SQLAlchemy detecte el cambio de estado (mutable array)
            current_uids = list(customer.billing_profile_uids)
            current_uids.append(clean_uid)
            customer.billing_profile_uids = current_uids
            
            db.commit()
            db.refresh(customer)
        
        return {
            "status": "success",
            "current_uids": customer.billing_profile_uids
        }
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))    

@router.delete("/{customer_id}/billing-profiles/{uid_to_remove}")
async def remove_customer_billing_uid(
    customer_id: int,
    uid_to_remove: str,
    db: Session = Depends(get_db),
    token_data: dict = Depends(verify_firebase_token)
):
    establishment_id = token_data.get('uid')
    
    customer = db.query(Customer).filter(
        Customer.id == customer_id,
        Customer.establishment_id == establishment_id
    ).first()

    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    try:
        if customer.billing_profile_uids and uid_to_remove in customer.billing_profile_uids:
            # Filtramos la lista para remover el UID
            current_uids = [u for u in customer.billing_profile_uids if u != uid_to_remove]
            customer.billing_profile_uids = current_uids
            
            db.commit()
            db.refresh(customer)
            
        return {
            "status": "success",
            "message": "Perfil desvinculado correctamente",
            "current_uids": customer.billing_profile_uids
        }
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
