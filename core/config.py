import os
import json
import boto3
from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict
from botocore.exceptions import ClientError
from pydantic import field_validator

# 1. Leer el .env explícitamente ANTES de que boto3 haga nada
load_dotenv()

def get_aws_secret():
    # 👇 ¡AQUÍ PONEMOS EL NOMBRE REAL DE TU SECRETO!
    secret_name = os.environ.get("AWS_SECRET_NAME")
    
    # 👇 Y AQUÍ LA REGIÓN CORRECTA (us-east-2)
    region_name = os.environ.get("AWS_DEFAULT_REGION")
    
    session = boto3.session.Session()
    client = session.client(
        service_name='secretsmanager', 
        region_name=region_name
    )

    try:
        response = client.get_secret_value(SecretId=secret_name)
        return json.loads(response['SecretString'])
    except ClientError as e:
        print(f"⚠️ Error conectando a AWS: {e}")
        return {}

class Settings(BaseSettings):
    DATABASE_URL: str 
    FIREBASE_CLIENT_EMAIL: str
    FIREBASE_PRIVATE_KEY_ID: str
    FIREBASE_PRIVATE_KEY: str
    FIREBASE_PROJECT_ID: str
    WEBHOOK_URL_NOTIFICATIONS: str 
    WEBHOOK_WHATSAPP_AUTH_PIN: str
    WEBHOOK_NEXT_APPOINTMENT_URL: str 
    ADMIN_API_KEY: str 
    STRIPE_SECRET_KEY: str
    STRIPE_PRICE_IDS: list[str]
    SUPERADMIN_API_KEY: str
    ALLOWED_SUPERADMIN_IPS: str
    ALLOWED_ADMIN_UIDS: str
    SYSTEM_KEY: str
    SENTRY_DSN: str
    KIPU_BASE_URL: str
    INTERNAL_WAPPTI_KEY: str
    SMTP_SERVER: str
    SMTP_PORT: int
    SMTP_USER: str
    SMTP_PASSWORD: str
    FROM_EMAIL: str
    DEBUG: bool = False

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    @field_validator("STRIPE_PRICE_IDS", mode="before")
    @classmethod
    def parse_stripe_prices(cls, v):
        if isinstance(v, str):
            return [price.strip() for price in v.split(",") if price.strip()]
        return v
"""
try:
    settings = Settings()
    print("✅ Configuración cargada desde archivo .env local")
except Exception as e:
    print("⚠️ Faltan variables locales, yendo a buscar a AWS Secrets Manager...")
    aws_secrets = get_aws_secret()
    settings = Settings(**aws_secrets)
    print("✅ Configuración cargada con éxito desde AWS")
"""
try:
    # Intento 1: Solo con .env local
    settings = Settings()
    print("✅ Configuración cargada desde archivo .env local")
except Exception:
    print("⚠️ Faltan variables locales, yendo a buscar a AWS Secrets Manager...")
    aws_secrets = get_aws_secret()
    
    try:
        settings = Settings(**aws_secrets)
        print("✅ Configuración cargada con éxito (Híbrida Local + AWS)")
    except Exception as final_error:
        print(f"❌ Error fatal de configuración: {final_error}")
        raise final_error    