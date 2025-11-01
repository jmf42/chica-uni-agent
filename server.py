import os
import asyncio
from typing import List, Dict, Any, Optional

import httpx
from fastapi import FastAPI, HTTPException, Header
from aiotdlib import Client, ClientSettings
from aiotdlib.api import types
from dotenv import load_dotenv

# =========================================================
# 1) Cargar entorno
# =========================================================
load_dotenv()

API_ID = int(os.getenv("AIOTDLIB_API_ID", 0))
API_HASH = os.getenv("AIOTDLIB_API_HASH", "")
PHONE = os.getenv("AIOTDLIB_PHONE_NUMBER", "")
SESSION = os.getenv("AIOTDLIB_SESSION_NAME", "chica_uni_session")

API_KEY = os.getenv("AGENT_API_KEY", "super_secret_local_key")

# opcional (si algún día usás bot)
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_TARGET_CHAT_ID = os.getenv("TELEGRAM_TARGET_CHAT_ID", "")

# =========================================================
# 2) Cliente TDLib
# =========================================================
settings = ClientSettings(
    api_id=API_ID,
    api_hash=API_HASH,
    phone_number=PHONE,
    session_name=SESSION,
)
td_client = Client(settings=settings)

CLIENT_STARTED: bool = False
ALL_CHATS: List[Dict[str, Any]] = []
STUDY_CHATS: List[Dict[str, Any]] = []

# ⚠️ diferencia:
# - SELF_USER_ID → id del usuario (María)
# - SELF_PRIVATE_CHAT_ID → id del chat privado con María (el que SÍ acepta send_message)
SELF_USER_ID: Optional[int] = None
SELF_PRIVATE_CHAT_ID: Optional[int] = None

# keywords de estudio
STUDY_KEYWORDS = [
    "uned",
    "psico",
    "psicología",
    "psicologia",
    "pec",
    "tfg",
    "apuntes",
    "asignaturas",
    "social aplicada",
    "alteración",
    "alteracion",
    "desarrollo",
    "lenguaje",
]


def is_study_chat(title: str) -> bool:
    t = title.lower()
    return any(k in t for k in STUDY_KEYWORDS)


def check_api_key(x_api_key: str | None):
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized")


# =========================================================
# 3) App
# =========================================================
app = FastAPI(title="Chica Uni AI Agent Bridge", version="2.3")


# =========================================================
# 4) Startup
# =========================================================
@app.on_event("startup")
async def startup():
    """
    - inicia la sesión de Telegram
    - obtiene el user (María)
    - crea/obtiene el chat privado consigo misma
    - precarga los grupos
    - filtra solo los de estudio
    """
    global CLIENT_STARTED, ALL_CHATS, STUDY_CHATS, SELF_USER_ID, SELF_PRIVATE_CHAT_ID

    if CLIENT_STARTED:
        return

    print("🚀 Iniciando TDLib...")
    await td_client.__aenter__()
    CLIENT_STARTED = True

    # 1) quién soy
    me = None
    for _ in range(10):
        try:
            me = await td_client.api.get_me()
            break
        except Exception as e:
            print(f"⏳ Esperando TDLib... {e}")
            await asyncio.sleep(1)

    if me is None:
        print("⚠️ No se pudo obtener el usuario actual.")
    else:
        SELF_USER_ID = me.id
        print(f"✅ Conectado como {me.first_name} ({me.id})")

        # 2) crear/obtener el chat privado con ella misma
        #    ESTE es el truco para evitar "Chat not found"
        try:
            self_chat = await td_client.api.create_private_chat(user_id=me.id, force=True)
            SELF_PRIVATE_CHAT_ID = self_chat.id
            print(f"🔒 Chat privado con María listo: {SELF_PRIVATE_CHAT_ID}")
        except Exception as e:
            print(f"❌ No se pudo crear el chat privado con la propia cuenta: {e}")

    # 3) precargar chats y filtrar
    try:
        chat_ids = await td_client.api.get_chats(limit=200)
        all_chats: List[Dict[str, Any]] = []
        study_chats: List[Dict[str, Any]] = []

        for chat_id in getattr(chat_ids, "chat_ids", []):
            try:
                chat = await td_client.api.get_chat(chat_id=chat_id)
                title = getattr(chat, "title", None) or getattr(chat, "first_name", "Sin título")
                chat_dict = {"id": chat.id, "title": title}
                all_chats.append(chat_dict)

                if is_study_chat(title):
                    study_chats.append(chat_dict)
            except Exception as e:
                print(f"⚠️ No se pudo cargar chat {chat_id}: {e}")

        ALL_CHATS = all_chats
        STUDY_CHATS = study_chats

        print(f"📚 Chats totales: {len(ALL_CHATS)}")
        print(f"📘 Chats de estudio detectados: {len(STUDY_CHATS)}")
        for c in STUDY_CHATS:
            print(f"   • {c['id']} - {c['title']}")
    except Exception as e:
        print(f"❌ Error precargando chats: {e}")


@app.on_event("shutdown")
async def shutdown():
    global CLIENT_STARTED
    print("🛑 Cerrando TDLib...")
    if CLIENT_STARTED:
        try:
            await td_client.__aexit__(None, None, None)
            print("✅ TDLib cerrado")
        except Exception as e:
            print(f"⚠️ Error al cerrar TDLib: {e}")
    CLIENT_STARTED = False


# =========================================================
# 5) Endpoints
# =========================================================
@app.get("/telegram/me")
async def get_me_info(x_api_key: str = Header(None)):
    """
    Para que vos verifiques que es realmente la cuenta de María.
    """
    check_api_key(x_api_key)

    if not CLIENT_STARTED:
        raise HTTPException(status_code=503, detail="Telegram client not initialized yet")

    me = await td_client.api.get_me()

    return {
        "id": me.id,
        "first_name": me.first_name,
        "last_name": me.last_name,
        # solo últimos 4 por seguridad
        "phone_ends_with": me.phone_number[-4:] if me.phone_number else None,
        "self_private_chat_id": SELF_PRIVATE_CHAT_ID,
    }


@app.get("/telegram/chats")
async def list_study_chats(x_api_key: str = Header(None)):
    """
    Solo los grupos de estudio.
    """
    check_api_key(x_api_key)

    if not CLIENT_STARTED:
        raise HTTPException(status_code=503, detail="Telegram client not initialized yet")

    return {"chats": STUDY_CHATS}


@app.get("/telegram/messages")
async def get_messages(
    chat_id: int,
    limit: int = 50,
    x_api_key: str = Header(None),
):
    """
    Lee mensajes SOLO de los grupos de estudio.
    """
    check_api_key(x_api_key)

    if not CLIENT_STARTED:
        raise HTTPException(status_code=503, detail="Telegram client not initialized yet")

    study_ids = {c["id"] for c in STUDY_CHATS}
    if chat_id not in study_ids:
        raise HTTPException(status_code=403, detail="This chat is not allowed for reading")

    try:
        try:
            await td_client.api.open_chat(chat_id=chat_id)
        except Exception as e:
            print(f"⚠️ open_chat falló para {chat_id}: {e}")

        history = await td_client.api.get_chat_history(
            chat_id=chat_id,
            from_message_id=0,
            offset=0,
            limit=min(limit, 100),
            only_local=False,
        )

        messages: List[str] = []
        for msg in getattr(history, "messages", []):
            content = msg.content
            if hasattr(content, "text") and hasattr(content.text, "text"):
                messages.append(content.text.text)

        return {
            "chat_id": chat_id,
            "messages": messages,
            "count": len(messages),
        }

    except Exception as e:
        import traceback
        print("❌ ERROR get_chat_history:\n", traceback.format_exc())
        return {"chat_id": chat_id, "messages": [], "error": str(e)}


@app.post("/telegram/send")
async def send_to_maria(
    body: dict,
    x_api_key: str = Header(None),
):
    """
    Envía SOLO a María (a su chat privado).
    NO acepta chat_id.
    NO acepta otros campos.
    """
    check_api_key(x_api_key)

    # aceptar solo 'text'
    allowed_keys = {"text"}
    extra = set(body.keys()) - allowed_keys
    if extra:
        raise HTTPException(status_code=400, detail="Only 'text' is allowed")

    text = body.get("text")
    if not text:
        raise HTTPException(status_code=400, detail="text is required")

    if len(text) > 1500:
        raise HTTPException(status_code=400, detail="text too long")

    if not CLIENT_STARTED:
        raise HTTPException(status_code=503, detail="Telegram client not initialized yet")

    # 1) si hay bot + destino → usar bot
    if TELEGRAM_BOT_TOKEN and TELEGRAM_TARGET_CHAT_ID:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                json={
                    "chat_id": int(TELEGRAM_TARGET_CHAT_ID),
                    "text": text,
                    "parse_mode": "Markdown",
                },
            )
        if r.status_code != 200:
            raise HTTPException(status_code=500, detail=r.text)
        print(f"📤 Enviado por BOT a {TELEGRAM_TARGET_CHAT_ID}: {text[:80]}...")
        return {"status": "sent", "via": "bot"}

    # 2) enviar con la cuenta de María
    global SELF_PRIVATE_CHAT_ID

    if SELF_PRIVATE_CHAT_ID is None:
        # puede pasar si recién levantaste el server o se limpió el cache
        if SELF_USER_ID is None:
            raise HTTPException(status_code=500, detail="SELF_USER_ID not initialized")
        # recreamos el chat privado
        self_chat = await td_client.api.create_private_chat(user_id=SELF_USER_ID, force=True)
        SELF_PRIVATE_CHAT_ID = self_chat.id
        print(f"♻️ Re-creado chat privado con María: {SELF_PRIVATE_CHAT_ID}")

    content = types.InputMessageText(
        text=types.FormattedText(
            text=text,
            entities=[],
        ),
        disable_web_page_preview=True,
        clear_draft=False,
    )

    try:
        await td_client.api.send_message(
            chat_id=SELF_PRIVATE_CHAT_ID,
            input_message_content=content,
        )
    except Exception as e:
        # si por alguna razón el chat desapareció, lo volvemos a crear 1 vez
        try:
            self_chat = await td_client.api.create_private_chat(user_id=SELF_USER_ID, force=True)
            SELF_PRIVATE_CHAT_ID = self_chat.id
            await td_client.api.send_message(
                chat_id=SELF_PRIVATE_CHAT_ID,
                input_message_content=content,
            )
        except Exception as e2:
            raise HTTPException(status_code=500, detail=str(e2))

    print(f"📤 Enviado a SELF PRIVATE CHAT ({SELF_PRIVATE_CHAT_ID}): {text[:80]}...")
    return {
        "status": "sent",
        "via": "tdlib",
        "chat_id": SELF_PRIVATE_CHAT_ID,
    }


# =========================================================
# 6) Main
# =========================================================
if __name__ == "__main__":
    import uvicorn

    uvicorn.run("server:app", host="127.0.0.1", port=8000, reload=True)
