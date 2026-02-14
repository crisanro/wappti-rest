import os
import httpx
from typing import Dict, Any

class WhatsAppService:
    def __init__(self):
        self.token = os.getenv("WHATSAPP_TOKEN")
        self.phone_id = os.getenv("WHATSAPP_PHONE_ID")
        self.version = os.getenv("WHATSAPP_VERSION", "v22.0")
        self.base_url = f"https://graph.facebook.com/{self.version}/{self.phone_id}/messages"
        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json"
        }

    async def _send(self, payload: Dict[str, Any]):
        async with httpx.AsyncClient() as client:
            response = await client.post(self.base_url, headers=self.headers, json=payload)
            return response.json()

    async def enviar_texto_cita(self, numero: str, nombre: str, lugar: str, fecha: str):
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": numero,
            "type": "text",
            "text": {
                "preview_url": True,
                "body": f"{nombre}, le informamos que su próxima cita {lugar} ha sido registrada para el {fecha}.\n\nSi requiere información adicional puede solicitarla al siguiente contacto."
            }
        }
        return await self._send(payload)

    async def enviar_contacto(self, numero: str, nombre_contacto: str, num_contacto: str):
        payload = {
            "messaging_product": "whatsapp",
            "to": numero,
            "type": "contacts",
            "contacts": [{
                "name": {
                    "formatted_name": nombre_contacto,
                    "first_name": nombre_contacto
                },
                "phones": [{
                    "phone": f"+{num_contacto}",
                    "type": "WORK",
                    "wa_id": num_contacto
                }]
            }]
        }
        return await self._send(payload)