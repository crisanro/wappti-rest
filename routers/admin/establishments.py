from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
# Importa aquí tu modelo de base de datos y la función para obtener la sesión
# from your_database_file import get_db, Establishment 

router = APIRouter()

@router.patch("/establishments/{establishment_id}/add-credits")
def add_credits_to_establishment(
    establishment_id: str, 
    payload: CreditReload, 
    db: Session = Depends(get_db)
):
    # 1. Buscar al establecimiento
    establishment = db.query(Establishment).filter(Establishment.id == establishment_id).first()
    
    if not establishment:
        raise HTTPException(status_code=404, detail="Establecimiento no encontrado")

    # 2. Lógica de suma
    # Manejamos el caso de que available_credits sea None por alguna razón
    current_credits = establishment.available_credits or 0
    establishment.available_credits = current_credits + payload.amount

    # 3. Guardar cambios
    try:
        db.commit()
        db.refresh(establishment)
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail="Error al actualizar los créditos")

    return {
        "message": "Créditos actualizados exitosamente",
        "establishment_id": establishment.id,
        "new_balance": establishment.available_credits
    }
