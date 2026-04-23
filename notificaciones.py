"""
Agenda Familiar Pfeng Gaspar
Notificaciones WhatsApp via Twilio
Ejecutado automáticamente por GitHub Actions cada 30 minutos
"""

import os
import json
from datetime import datetime, timedelta, timezone
from supabase import create_client
from twilio.rest import Client

# ── Credenciales (vienen de GitHub Secrets) ──────────────────────────────────
SUPABASE_URL  = os.environ["SUPABASE_URL"]
SUPABASE_KEY  = os.environ["SUPABASE_KEY"]
TWILIO_SID    = os.environ["TWILIO_SID"]
TWILIO_TOKEN  = os.environ["TWILIO_TOKEN"]
TWILIO_WA_NUM = "whatsapp:+14155238886"  # Número sandbox Twilio

# ── Clientes ──────────────────────────────────────────────────────────────────
db     = create_client(SUPABASE_URL, SUPABASE_KEY)
twilio = Client(TWILIO_SID, TWILIO_TOKEN)

# ── Ventanas de notificación ──────────────────────────────────────────────────
VENTANAS = [
    {"minutos": 1440, "etiqueta": "mañana"},   # 24 horas antes
    {"minutos": 60,   "etiqueta": "en 1 hora"},
    {"minutos": 30,   "etiqueta": "en 30 minutos"},
]
TOLERANCIA_MIN = 15  # margen para no perder ejecuciones


def ya_enviada(evento_id: str, tipo: str) -> bool:
    """Revisa si ya se envió esta notificación para no duplicar."""
    res = db.table("notificaciones").select("id").eq("evento_id", evento_id).eq("tipo", tipo).eq("whatsapp_enviado", True).execute()
    return len(res.data) > 0


def registrar_enviada(evento_id: str, tipo: str):
    """Marca la notificación como enviada en Supabase."""
    db.table("notificaciones").insert({
        "evento_id": evento_id,
        "tipo": tipo,
        "whatsapp_enviado": True,
        "minutos_antes": int(tipo.replace("min_", "")),
        "enviada": True,
    }).execute()


def formatear_mensaje(nombre: str, titulo: str, etiqueta: str, fecha: datetime) -> str:
    dia  = fecha.strftime("%A %d de %B").capitalize()
    hora = fecha.strftime("%H:%M")
    return (
        f"🗓️ *Agenda Familiar*\n\n"
        f"Hola {nombre}! Te recordamos que *{titulo}* es *{etiqueta}*.\n\n"
        f"📅 {dia}\n"
        f"🕐 {hora} hrs\n\n"
        f"_Agenda Pfeng Gaspar_ 👨‍👩‍👧‍👦"
    )


def enviar_whatsapp(telefono: str, mensaje: str):
    try:
        twilio.messages.create(
            from_=TWILIO_WA_NUM,
            to=f"whatsapp:{telefono}",
            body=mensaje,
        )
        print(f"  ✓ Enviado a {telefono}")
    except Exception as e:
        print(f"  ✗ Error enviando a {telefono}: {e}")


def calcular_ocurrencias_proximas(evento: dict, ahora: datetime, margen_min: int) -> list[datetime]:
    """
    Dado un evento (posiblemente repetitivo), retorna las fechas de ocurrencia
    que caen dentro de la ventana [ahora, ahora + margen_min minutos].
    """
    fecha_str = evento["fecha_inicio"][:10]
    hora_str  = evento.get("hora") or evento["fecha_inicio"][11:16]
    repeat    = evento.get("repeat_type") or "none"
    repeat_end= evento.get("repeat_end")

    # Fecha/hora base del evento en timezone de Chile (UTC-3 o UTC-4)
    # Usamos UTC para consistencia — ajusta si necesitas timezone local
    try:
        base = datetime.strptime(f"{fecha_str} {hora_str}", "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
    except Exception:
        return []

    limite_sup = ahora + timedelta(minutes=margen_min)
    limite_inf = ahora

    ocurrencias = []

    if repeat == "none":
        if limite_inf <= base <= limite_sup:
            ocurrencias.append(base)
        return ocurrencias

    # Generar ocurrencias hasta 2 días adelante
    cursor = base
    tope   = ahora + timedelta(days=2)
    end    = datetime.strptime(repeat_end, "%Y-%m-%d").replace(tzinfo=timezone.utc) if repeat_end else tope

    while cursor <= min(tope, end):
        if limite_inf <= cursor <= limite_sup:
            ocurrencias.append(cursor)
        if repeat == "daily":
            cursor += timedelta(days=1)
        elif repeat == "weekly":
            cursor += timedelta(weeks=1)
        elif repeat == "biweekly":
            cursor += timedelta(weeks=2)
        elif repeat == "monthly":
            mes  = cursor.month + 1 if cursor.month < 12 else 1
            anio = cursor.year + (1 if cursor.month == 12 else 0)
            try:
                cursor = cursor.replace(year=anio, month=mes)
            except ValueError:
                break
        elif repeat == "yearly":
            try:
                cursor = cursor.replace(year=cursor.year + 1)
            except ValueError:
                break
        else:
            break

    return ocurrencias


def main():
    ahora = datetime.now(timezone.utc)
    print(f"\n🔔 Revisando notificaciones — {ahora.strftime('%Y-%m-%d %H:%M')} UTC\n")

    # Cargar todos los eventos con integrantes
    eventos_res = db.table("eventos").select("*").execute()
    integr_res  = db.table("integrantes").select("*").execute()

    integrantes_map = {str(i["id"]): i for i in integr_res.data}

    notif_enviadas = 0

    for evento in eventos_res.data:
        titulo      = evento["titulo"]
        miembros_ids = evento.get("integrantes") or []

        for ventana in VENTANAS:
            minutos  = ventana["minutos"]
            etiqueta = ventana["etiqueta"]
            tipo_key = f"min_{minutos}"

            # Buscar si hay ocurrencia en esta ventana
            objetivo = ahora + timedelta(minutes=minutos)
            margen   = TOLERANCIA_MIN

            ocurrencias = calcular_ocurrencias_proximas(
                evento,
                objetivo - timedelta(minutes=margen),
                margen * 2,
            )

            if not ocurrencias:
                continue

            # Verificar si ya se envió (por evento + tipo)
            if ya_enviada(str(evento["id"]), tipo_key):
                print(f"  ↷ Ya enviada: {titulo} ({etiqueta})")
                continue

            # Enviar a cada integrante del evento
            for miembro_id in miembros_ids:
                integrante = integrantes_map.get(str(miembro_id))
                if not integrante:
                    continue
                telefono = integrante.get("telefono")
                if not telefono:
                    print(f"  ⚠ Sin teléfono: {integrante['nombre']}")
                    continue

                mensaje = formatear_mensaje(
                    integrante["nombre"],
                    titulo,
                    etiqueta,
                    ocurrencias[0],
                )
                print(f"  → {titulo} | {integrante['nombre']} | {etiqueta}")
                enviar_whatsapp(telefono, mensaje)
                notif_enviadas += 1

            # Registrar como enviada
            registrar_enviada(str(evento["id"]), tipo_key)

    print(f"\n✅ Listo. {notif_enviadas} notificaciones enviadas.\n")


if __name__ == "__main__":
    main()
