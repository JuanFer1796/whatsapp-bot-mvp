from fastapi import FastAPI
from pydantic import BaseModel
from typing import Any, Dict, Optional, Tuple
import os
import re
import httpx
import psycopg

app = FastAPI()

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://bot:botpass@postgres:5432/botdb")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://ollama:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1:8b")


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


def ollama_chat(user_text: str, intent: str) -> str:
    system = (
        "Sos un asistente para un chatbot de WhatsApp. "
        "Respondé en español, breve y claro. "
        "Si falta info, hacé 1-2 preguntas máximo. "
        "No inventés precios ni políticas; si no hay datos, pedí detalle."
    )

    if intent == "VENTA":
        system += (
            " Tu objetivo es avanzar una venta con flujo estructurado: "
            "(1) entender necesidad, (2) pedir 1 dato clave, (3) proponer siguiente paso."
        )

    payload = {
        "model": OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user_text},
        ],
        "stream": False,
        "options": {"temperature": 0.3, "num_predict": 140},
    }

    r = httpx.post(f"{OLLAMA_BASE_URL}/api/chat", json=payload, timeout=60.0)
    r.raise_for_status()
    return r.json()["message"]["content"]


# -------------------------
# Postgres helpers
# -------------------------
def db_conn():
    # psycopg v3 soporta URLs tipo postgresql+psycopg://... (SQLAlchemy) pero preferimos URL simple.
    # Si viene con "postgresql+psycopg://", lo normalizamos.
    dsn = DATABASE_URL.replace("postgresql+psycopg://", "postgresql://")
    return psycopg.connect(dsn)


def get_or_create_lead(phone: str) -> Dict[str, Any]:
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("select phone, stage, service, customer_type, city, budget_gtq from leads where phone=%s", (phone,))
            row = cur.fetchone()
            if row:
                return {
                    "phone": row[0], "stage": row[1], "service": row[2],
                    "customer_type": row[3], "city": row[4], "budget_gtq": row[5],
                }
            cur.execute(
                "insert into leads(phone, stage) values (%s, 1) returning phone, stage, service, customer_type, city, budget_gtq",
                (phone,),
            )
            row = cur.fetchone()
            conn.commit()
            return {
                "phone": row[0], "stage": row[1], "service": row[2],
                "customer_type": row[3], "city": row[4], "budget_gtq": row[5],
            }


def update_lead(phone: str, **fields):
    if not fields:
        return
    cols = []
    vals = []
    for k, v in fields.items():
        cols.append(f"{k}=%s")
        vals.append(v)
    cols.append("updated_at=now()")
    q = f"update leads set {', '.join(cols)} where phone=%s"
    vals.append(phone)

    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(q, tuple(vals))
        conn.commit()


def reset_lead(phone: str):
    with db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update leads set stage=1, service=null, customer_type=null, city=null, budget_gtq=null, updated_at=now() where phone=%s",
                (phone,),
            )
        conn.commit()


# -------------------------
# Parsing helpers (MVP)
# -------------------------
def parse_customer_type(text: str) -> Optional[str]:
    t = text.lower()
    if "empresa" in t or "negocio" in t:
        return "empresa"
    if "persona" in t or "particular" in t or "individual" in t:
        return "persona"
    return None


def parse_budget_gtq(text: str) -> Optional[int]:
    # agarra el primer número "grande" (ej 5000, 12,000)
    m = re.search(r"(\d[\d,\.]{2,})", text)
    if not m:
        return None
    raw = m.group(1).replace(",", "").replace(".", "")
    try:
        val = int(raw)
        # filtro básico para evitar que "2026" se tome como presupuesto si es muy bajo/alto; ajustalo a tu gusto
        if 100 <= val <= 5_000_000:
            return val
    except ValueError:
        pass
    return None


def handle_venta(phone: str, text: str) -> Tuple[str, int]:
    # comandos de control
    t = text.strip().lower()
    if t in {"reset", "reiniciar", "cancelar"}:
        reset_lead(phone)
        return ("Listo, reinicié la cotización. ¿Qué servicio querés cotizar?", 1)

    lead = get_or_create_lead(phone)
    stage = int(lead["stage"] or 1)

    # Stage 1: servicio
    if stage == 1:
        update_lead(phone, service=text, stage=2)
        return ("Perfecto. ¿Es para una *empresa* o para una *persona* (particular)?", 2)

    # Stage 2: tipo de cliente
    if stage == 2:
        ct = parse_customer_type(text)
        if not ct:
            return ("¿Me confirmás si es *empresa* o *persona* (particular)?", 2)
        update_lead(phone, customer_type=ct, stage=3)
        return ("Buenísimo. ¿En qué ciudad/país sería el servicio?", 3)

    # Stage 3: ciudad
    if stage == 3:
        update_lead(phone, city=text, stage=4)
        return ("Gracias. ¿Qué presupuesto aproximado tenés (en GTQ)?", 4)

    # Stage 4: presupuesto
    if stage == 4:
        budget = parse_budget_gtq(text)
        if budget is None:
            return ("¿Me das un número aproximado de presupuesto en GTQ? (ej: 5000)", 4)
        update_lead(phone, budget_gtq=budget, stage=5)
        return ("Listo ✅ Ya armé el lead. ¿Querés que te contacte por este mismo medio? (sí/no)", 5)

    # Stage 5: cierre simple
    if stage >= 5:
        if "si" in t or "sí" in t:
            return ("Perfecto. Quedó registrado y te contacto por acá. Si querés reiniciar: escribí *reset*.", stage)
        if "no" in t:
            return ("Ok. Decime por dónde preferís que te contacten (correo o teléfono) y lo agrego.", stage)
        return ("¿Te contacto por este mismo medio? (sí/no). Si querés reiniciar: *reset*.", stage)


@app.post("/webhook/n8n")
def webhook_from_n8n(payload: N8NPayload):
    body = payload.body or {}

    phone = (body.get("phone") or "local").strip()
    text = (body.get("text") or "").strip()
    msg = text.lower()

    if not text:
        return {"intent": "FAQ", "reply": "¿Me repetís tu consulta en una frase?"}

    # HUMANO
    if any(k in msg for k in ["asesor", "humano", "agente"]):
        return {"intent": "HUMANO", "reply": "Perfecto. Te conecto con un asesor."}

    # VENTA por keywords
    if any(k in msg for k in ["precio", "cotizar", "cotización", "servicio", "comprar"]):
        reply, stage = handle_venta(phone, text)
        return {"intent": "VENTA", "reply": reply, "stage": stage}

    # FAQ con LLM
    try:
        reply = ollama_chat(text, intent="FAQ")
    except Exception:
        reply = "Ahorita tengo un problema técnico. ¿Podés intentar de nuevo en un momento?"

    return {"intent": "FAQ", "reply": reply}
