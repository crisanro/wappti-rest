from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import os
from dotenv import load_dotenv

load_dotenv()

# Obtenemos la URL de la base de datos
SQLALCHEMY_DATABASE_URL = os.getenv("DATABASE_URL")

# --- CONFIGURACIÓN DEL ENGINE CON POOLING ---
# Optimizamos para un servidor de 8GB compartido y 0.5 vCPU
engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    # 1. pool_size: Mantiene 5 conexiones abiertas listas para usar. 
    # Es un número bajo para no saturar la RAM de tu servidor compartido.
    pool_size=5, 
    
    # 2. max_overflow: En un pico de tráfico (ej. muchos pagos de Stripe), 
    # permite abrir hasta 5 conexiones extra temporales. Total: 10.
    max_overflow=5,
    
    # 3. pool_timeout: Si todas las conexiones están ocupadas, espera 30 seg 
    # antes de dar un error al usuario.
    pool_timeout=30,
    
    # 4. pool_recycle: Reinicia las conexiones cada 30 min para evitar que 
    # la base de datos las corte por inactividad.
    pool_recycle=1800,
    
    # 5. pool_pre_ping: Revisa si la conexión es válida antes de cada uso. 
    # Indispensable para recuperarse de micro-cortes del servidor.
    pool_pre_ping=True
)

# Configuración de la factoría de sesiones
# autoflush=False evita que se guarden cambios accidentales antes del commit
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Clase base para los modelos
Base = declarative_base()

# Dependencia para las rutas de FastAPI
def get_db():
    """ 
    Provee una sesión de base de datos para cada request y asegura 
    su cierre al terminar para liberar espacio en el pool.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        # Crucial: cierra la sesión para que la conexión vuelva al "pool"
        # y pueda ser usada por otro usuario u otro proceso.
        db.close()