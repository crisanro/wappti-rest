from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from schemas.admin.establishments import CreditReload
from core.database import get_db
from models import Establishment

router = APIRouter()

@router.patch("/add-credits/{establishment_id}") # Quitamos el slash final
def add_credits_to_establishment(
    establishment_id: str, 
    payload: CreditReload, 
    db: Session = Depends(get_db)
):
    # 1. Buscar al establecimiento
    establishment = db.query(Establishment).filter(Establishment.id == establishment_id).first()
    
    if not establishment:
        raise HTTPException(status_code=404, detail="Establecimiento no encontrado")

    # 2. Lógica de suma (Atómica para evitar errores de concurrencia)
    establishment.available_credits += payload.amount 

    # 3. Guardar cambios
    try:
        db.commit()
        db.refresh(establishment)
    except Exception as e:
        db.rollback()
        # Loggear el error 'e' aquí sería ideal
        raise HTTPException(status_code=500, detail="Error al actualizar los créditos")

    return {
        "status": "success",
        "message": f"Se han sumado {payload.amount} créditos.",
        "establishment_id": establishment.id,
        "new_balance": establishment.available_credits
    }
