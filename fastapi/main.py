from fastapi import FastAPI
from pydantic import BaseModel
from typing import Any, Dict, Optional

app = FastAPI()

@app.get("/health")
def health():
    return {"status": "ok"}


class N8NPayload(BaseModel):
    headers: Optional[Dict[str, Any]] = None
    params: Optional[Dict[str, Any]] = None
    query: Optional[Dict[str, Any]] = None
    body: Optional[Dict[str, Any]] = None
    webhookUrl: Optional[str] = None
    executionMode: Optional[str] = None


@app.post("/webhook/n8n")
def webhook_from_n8n(payload: N8NPayload):
    body = payload.body or {}

    phone = body.get("phone", "+50200000000")
    text = body.get("text", "")
    message_id = body.get("message_id", "local-test")

    msg = text.lower()

    if any(k in msg for k in ["asesor", "humano", "agente"]):
        return {"intent": "HUMANO", "reply": "Perfecto. Te conecto con un asesor."}

    if any(k in msg for k in ["precio", "cotizar", "cotización", "servicio", "comprar"]):
        return {"intent": "VENTA", "reply": "¡Excelente! ¿Qué servicio te interesa?"}

    return {"intent": "FAQ", "reply": "Contame un poco más para ayudarte. Si querés, te conecto con un asesor."}
