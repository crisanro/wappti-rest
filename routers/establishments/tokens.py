from fastapi import APIRouter, Depends, HTTPException, status, Request, Response
from sqlalchemy.orm import Session

# Tus imports específicos
from core.database import get_db
from core.auth import verify_firebase_token
from core.utils import register_action_log
from models import EstablishmentToken
from schemas.business import TokenKeyPayload
from core.utils import encrypt_value # Asumiendo que ahí pondrás la lógica de cifrado

router = APIRouter()

@router.get("/", response_model=list[dict])
def list_my_tokens(
    response: Response, # Añadimos esto
    db: Session = Depends(get_db),
    token_data: dict = Depends(verify_firebase_token)
):
    establishment_id = token_data.get('uid')
    
    tokens = db.query(EstablishmentToken).filter(
        EstablishmentToken.establishment_id == establishment_id
    ).all()
    
    # Enviamos el conteo en un header personalizado
    response.headers["X-Total-Count"] = str(len(tokens))
    
    return [
        {"id": t.id, "provider": t.provider, "created_at": t.created_at} 
        for t in tokens
    ]

@router.post("/")
async def save_secure_token(
    payload: TokenKeyPayload, 
    request: Request,
    db: Session = Depends(get_db),
    token_data: dict = Depends(verify_firebase_token)
):
    establishment_id = token_data.get('uid')
    provider_clean = payload.provider.strip().lower()
    
    # 1. VERIFICACIÓN DE EXISTENCIA PREVIA
    # Buscamos si ya existe el token para este proveedor
    existing_token = db.query(EstablishmentToken).filter(
        EstablishmentToken.establishment_id == establishment_id,
        EstablishmentToken.provider == provider_clean
    ).first()

    # REGLA DE NEGOCIO: Si existe, rechazamos la creación.
    if existing_token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, 
            detail=f"TOKEN_ALREADY_EXISTS. Please delete the existing {provider_clean} token before adding a new one."
        )

    # 2. PROCESO DE CIFRADO
    encrypted_str = encrypt_value(payload.token_value)

    # 3. CREACIÓN DEL NUEVO TOKEN
    new_db_token = EstablishmentToken(
        establishment_id=establishment_id,
        provider=provider_clean,
        encrypted_token=encrypted_str
    )
    
    try:
        db.add(new_db_token)
        db.commit()
        
        # Auditoría
        register_action_log(
            db, 
            establishment_id=establishment_id, 
            action=f"CREATE_TOKEN_{provider_clean.upper()}", 
            method=request.method, 
            path=request.url.path, 
            request=request,
            payload={"provider": provider_clean} 
        )
        
        return {"status": "success", "message": f"Token para {provider_clean} creado exitosamente."}
        
    except Exception as e:
        db.rollback()
        print(f"❌ Error al crear token: {str(e)}")
        raise HTTPException(status_code=500, detail="INTERNAL_SERVER_ERROR_ON_SAVE")
    

@router.delete("/{token_id}")
async def delete_token(
    token_id: int, # Ahora recibimos el ID numérico
    request: Request,
    db: Session = Depends(get_db),
    token_data: dict = Depends(verify_firebase_token)
):
    """
    Elimina un token específico por su ID único.
    """
    establishment_id = token_data.get('uid')
    
    # Buscamos el token por su ID y verificamos que pertenezca al establecimiento
    db_token = db.query(EstablishmentToken).filter(
        EstablishmentToken.id == token_id,
        EstablishmentToken.establishment_id == establishment_id
    ).first()

    if not db_token:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, 
            detail="TOKEN_NOT_FOUND_OR_ACCESS_DENIED"
        )

    # Guardamos el nombre del provider antes de borrarlo para el log
    provider_name = db_token.provider

    try:
        db.delete(db_token)
        db.commit()
        
        # Registramos la acción en el log de auditoría
        register_action_log(
            db, 
            establishment_id=establishment_id, 
            action=f"DELETE_TOKEN_{provider_name.upper()}", 
            method=request.method, 
            path=request.url.path, 
            request=request,
            payload={"token_id": token_id, "provider": provider_name}
        )
        
        return {"status": "success", "message": f"Token {provider_name} eliminado con éxito."}

    except Exception as e:
        db.rollback()
        print(f"❌ Error al eliminar token: {str(e)}")
        raise HTTPException(status_code=500, detail="INTERNAL_SERVER_ERROR")
