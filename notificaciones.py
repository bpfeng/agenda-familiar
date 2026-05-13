"""
Agenda Familiar Pfeng Gaspar
Notificaciones WhatsApp via Twilio
Ejecutado automáticamente por GitHub Actions cada 30 minutos
"""

import os
from datetime import datetime, timedelta, timezone
from supabase import create_client
from twilio.rest import Client

# ── Credenciales ──────────────────────────────────────────────────────────────
SUPABASE_URL  = os.environ["SUPABASE_URL"]
SUPABASE_KEY  = os.environ["SUPABASE_KEY"]
TWILIO_SID    = os.environ["TWILIO_SID"]
TWILIO_TOKEN  = os.environ["TWILIO_TOKEN"]
TWILIO_WA_NUM = "whatsapp:+14155238886"

# ── Clientes ──────────────────────────────────────────────────────────────────
db     = create_client(SUPABASE_URL, SUPABASE_KEY)
twilio = Client(TWILIO_SID, TWILIO_TOKEN)

# ── Ventanas de notificación ──────────────────────────────────────────────────
VENTANAS = [
    {"minutos": 1440, "etiqueta": "mañana"},
    {"minutos": 60,   "etiqueta": "en 1 hora"},
    {"minutos": 30,   "etiqueta": "en 30 minutos"},
]
TOLERANCIA_MIN = 14


def proxima_ocurrencia(evento: dict, desde: datetime):
    hora_str   = evento.get("hora") or evento["fecha_inicio"][11:16]
    repeat     = evento.get("repeat_type") or "none"
    repeat_end = evento.get("repeat_end")

    try:
        # La app guarda la hora en hora Chile (sin convertir a UTC)
        # Chile invierno = UTC-4, verano = UTC-3
        # Usamos UTC-4 como estándar
        CHILE_OFFSET = timedelta(hours=4)
        base = datetime.strptime(
            f"{evento['fecha_inicio'][:10]} {hora_str}", "%Y-%m-%d %H:%M"
        ).replace(tzinfo=timezone.utc) + CHILE_OFFSET  # convertir Chile → UTC
    except Exception:
        return None

    end = (datetime.strptime(repeat_end, "%Y-%m-%d").replace(tzinfo=timezone.utc) + timedelta(hours=4)
           if repeat_end else desde + timedelta(days=366))

    if repeat == "none":
        return base if base >= desde else None

    if repeat == "daily":
        diff = (desde - base).total_seconds()
        if diff <= 0:
            return base
        days = int(diff / 86400) + 1
        c = base + timedelta(days=days)
        return c if c <= end else None

    if repeat == "weekly":
        diff_days = (desde.date() - base.date()).days
        if diff_days <= 0:
            return base
        weeks = (diff_days // 7) + 1
        c = base + timedelta(weeks=weeks)
        while c.date() < desde.date():
            c += timedelta(weeks=1)
        return c if c <= end else None

    if repeat == "biweekly":
        diff_days = (desde.date() - base.date()).days
        if diff_days <= 0:
            return base
        periods = (diff_days // 14) + 1
        c = base + timedelta(weeks=periods * 2)
        while c.date() < desde.date():
            c += timedelta(weeks=2)
        return c if c <= end else None

    if repeat == "monthly":
        c = base
        while c < desde:
            mes  = c.month + 1 if c.month < 12 else 1
            anio = c.year + (1 if c.month == 12 else 0)
            try:
                c = c.replace(year=anio, month=mes)
            except ValueError:
                break
        return c if c <= end else None

    if repeat == "yearly":
        c = base
        while c < desde:
            try:
                c = c.replace(year=c.year + 1)
            except ValueError:
                break
        return c if c <= end else None

    return None


def clave_notificacion(evento_id, ocurrencia, minutos):
    return f"{evento_id}_{ocurrencia.strftime('%Y%m%d%H%M')}_{minutos}"


def ya_enviada(clave):
    res = db.table("notificaciones").select("id").eq("tipo", clave).eq("whatsapp_enviado", True).execute()
    return len(res.data) > 0


def registrar_enviada(evento_id, clave, minutos):
    db.table("notificaciones").insert({
        "evento_id": evento_id, "tipo": clave,
        "whatsapp_enviado": True, "minutos_antes": minutos, "enviada": True,
    }).execute()


def formatear_mensaje(nombre, titulo, etiqueta, fecha):
    DIAS  = ["lunes","martes","miércoles","jueves","viernes","sábado","domingo"]
    MESES = ["enero","febrero","marzo","abril","mayo","junio",
             "julio","agosto","septiembre","octubre","noviembre","diciembre"]
    fecha_cl = fecha - timedelta(hours=4)
    return (
        f"🗓️ *Agenda Familiar Pfeng Gaspar*\n\n"
        f"Hola {nombre}! Recordatorio: *{titulo}* es *{etiqueta}*.\n\n"
        f"📅 {DIAS[fecha_cl.weekday()].capitalize()} {fecha_cl.day} de {MESES[fecha_cl.month-1]}\n"
        f"🕐 {fecha_cl.strftime('%H:%M')} hrs\n\n"
        f"_Familia Pfeng Gaspar_ 👨‍👩‍👧‍👦"
    )


def enviar_whatsapp(telefono, mensaje):
    try:
        twilio.messages.create(from_=TWILIO_WA_NUM, to=f"whatsapp:{telefono}", body=mensaje)
        print(f"    ✓ Enviado a {telefono}")
    except Exception as e:
        print(f"    ✗ Error: {e}")


def main():
    ahora = datetime.now(timezone.utc)
    print(f"\n🔔 Revisando notificaciones — {ahora.strftime('%Y-%m-%d %H:%M')} UTC\n")

    eventos_res = db.table("eventos").select("*").execute()
    integr_res  = db.table("integrantes").select("*").execute()
    integrantes_map = {str(i["id"]): i for i in integr_res.data}
    notif_enviadas = 0

    for evento in eventos_res.data:
        titulo       = evento["titulo"]
        miembros_ids = evento.get("integrantes") or []

        for ventana in VENTANAS:
            minutos  = ventana["minutos"]
            etiqueta = ventana["etiqueta"]
            objetivo = ahora + timedelta(minutes=minutos)
            desde    = objetivo - timedelta(minutes=TOLERANCIA_MIN)

            ocurrencia = proxima_ocurrencia(evento, desde)
            if not ocurrencia:
                continue

            diff = abs((ocurrencia - objetivo).total_seconds() / 60)
            if diff > TOLERANCIA_MIN:
                continue

            clave = clave_notificacion(str(evento["id"]), ocurrencia, minutos)
            if ya_enviada(clave):
                print(f"  ↷ Ya enviada: {titulo} ({etiqueta})")
                continue

            print(f"  → {titulo} | {etiqueta} | {ocurrencia.strftime('%Y-%m-%d %H:%M')} UTC")

            for miembro_id in miembros_ids:
                integrante = integrantes_map.get(str(miembro_id))
                if not integrante:
                    continue
                telefono = integrante.get("telefono")
                if not telefono:
                    print(f"    ⚠ Sin teléfono: {integrante['nombre']}")
                    continue
                enviar_whatsapp(telefono, formatear_mensaje(integrante["nombre"], titulo, etiqueta, ocurrencia))
                notif_enviadas += 1

            registrar_enviada(str(evento["id"]), clave, minutos)

    print(f"\n✅ Listo. {notif_enviadas} notificaciones enviadas.\n")


if __name__ == "__main__":
    main()
