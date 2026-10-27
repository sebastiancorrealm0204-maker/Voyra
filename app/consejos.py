"""Consejos proactivos de llegada y de noche: avisos cortos, seguros y deterministas (sin modelo de lenguaje).

El scheduler llama a `revisar(trip, ahora)` en cada tick. Según lo que el usuario subió (hora de llegada, planes con hora),
decide si toca avisar y manda una notificación que aconseja o invita a usar la app:

- llegada_pre_*     (~1 h antes de aterrizar): bienvenida y cómo salir del aeropuerto; si llega de noche, advertencia fuerte.
- aterrizaje_*      (~25 min después de la hora de llegada): «bienvenido, ¿te ayudo a llegar al hotel?».
- noche_plan        (2 h a 30 min antes de un plan nocturno o de un bar): pedir taxi por app, jamás uno de la calle.
- vuelta_tarde      (22:30 en adelante, si hubo plan nocturno hoy): volver con seguridad.
- primera_noche     (21:00 del primer día, sin plan nocturno): invitación a preguntar por cena y recordatorio de seguridad.
- tip_dia           (mediodía, si no ha abierto la app): una idea concreta de lo que puede pedirle al Companion.

Cada aviso sale UNA vez por día local (dedup en proactive_log) y también queda en el chat, para que al tocar la
notificación el usuario encuentre el mensaje completo.
"""
import logging
import re
from datetime import datetime, timedelta

from . import db, reloj

log = logging.getLogger("companion.consejos")

HORA_NOCHE = 20.0            # un plan desde esta hora (o de madrugada) cuenta como plan nocturno
LLEGADA_NOCHE = (21.0, 5.0)  # aterrizar entre las 21:00 y las 05:00 = llegada nocturna

_FIESTA = re.compile(r"\b(fiesta|rumba|discoteca|club|bar|parranda|trago|coctel|cocktail|party|nightclub|pub|salsa|antro)\b", re.I)

# kind → idioma → (título, cuerpo). Los títulos dicen qué pasa (el sistema ya muestra «Companion» como origen).
TXT: dict[str, dict[str, tuple[str, str]]] = {
    "llegada_pre_dia": {
        "es": ("Pronto aterrizas", "Bienvenido a {ciudad}. Al salir, pide tu transporte por app (Uber, inDrive, Cabify) o en la fila oficial de taxis del aeropuerto. Pregúntame cómo llegar a tu hotel."),
        "en": ("You land soon", "Welcome to {ciudad}. When you exit, book your ride in an app (Uber, inDrive, Cabify) or use the official airport taxi line. Ask me how to reach your hotel."),
        "pt": ("Você pousa em breve", "Bem-vindo a {ciudad}. Ao sair, peça seu transporte por app (Uber, inDrive, Cabify) ou use a fila oficial de táxis do aeroporto. Pergunte-me como chegar ao hotel."),
        "fr": ("Tu atterris bientôt", "Bienvenue à {ciudad}. En sortant, commande ton trajet par appli (Uber, inDrive, Cabify) ou utilise la file officielle de taxis de l'aéroport. Demande-moi comment rejoindre ton hôtel."),
        "de": ("Bald landest du", "Willkommen in {ciudad}. Bestell dir nach der Landung eine Fahrt per App (Uber, inDrive, Cabify) oder nutze den offiziellen Taxistand. Frag mich, wie du zum Hotel kommst."),
        "it": ("Stai per atterrare", "Benvenuto a {ciudad}. All'uscita prenota il trasporto con un'app (Uber, inDrive, Cabify) o usa la fila ufficiale dei taxi dell'aeroporto. Chiedimi come arrivare in hotel."),
    },
    "llegada_pre_noche": {
        "es": ("Aterrizas de noche", "Llegas tarde a {ciudad}. Pide tu transporte por app o en el puesto oficial del aeropuerto. Jamás tomes un taxi de la calle ni aceptes ofertas de gente que te aborde."),
        "en": ("You land at night", "You arrive late in {ciudad}. Book your ride in an app or at the official airport desk. Never take a street taxi or accept offers from people who approach you."),
        "pt": ("Você pousa à noite", "Você chega tarde a {ciudad}. Peça o transporte por app ou no balcão oficial do aeroporto. Jamais pegue um táxi na rua nem aceite ofertas de quem te abordar."),
        "fr": ("Tu atterris de nuit", "Tu arrives tard à {ciudad}. Commande ton trajet par appli ou au comptoir officiel de l'aéroport. Ne prends jamais un taxi dans la rue ni les offres de personnes qui t'abordent."),
        "de": ("Du landest nachts", "Du kommst spät in {ciudad} an. Bestell die Fahrt per App oder am offiziellen Schalter des Flughafens. Steig niemals in ein Taxi von der Straße und nimm keine Angebote von Ansprechenden an."),
        "it": ("Atterri di notte", "Arrivi tardi a {ciudad}. Prenota il trasporto con un'app o al banco ufficiale dell'aeroporto. Mai un taxi preso in strada né offerte di chi ti avvicina."),
    },
    "aterrizaje_dia": {
        "es": ("Bienvenido a {ciudad}", "Ya debes estar en tierra. ¿Te ayudo a llegar al hotel? Dime y te muestro taxi o app, TransMilenio y el tráfico de ahora."),
        "en": ("Welcome to {ciudad}", "You should be on the ground now. Want help getting to your hotel? Tell me and I'll show taxi or app, public transit and current traffic."),
        "pt": ("Bem-vindo a {ciudad}", "Você já deve estar em solo. Quer ajuda para chegar ao hotel? Me diga e mostro táxi ou app, transporte público e o trânsito de agora."),
        "fr": ("Bienvenue à {ciudad}", "Tu dois être au sol. Je t'aide à rejoindre l'hôtel ? Dis-moi et je te montre taxi ou appli, transports et la circulation actuelle."),
        "de": ("Willkommen in {ciudad}", "Du bist jetzt wohl gelandet. Soll ich dir den Weg zum Hotel zeigen? Sag Bescheid: Taxi oder App, ÖPNV und der aktuelle Verkehr."),
        "it": ("Benvenuto a {ciudad}", "Dovresti essere a terra. Ti aiuto ad arrivare in hotel? Dimmelo e ti mostro taxi o app, trasporti e il traffico di ora."),
    },
    "aterrizaje_noche": {
        "es": ("Bienvenido a {ciudad}", "Ya debes estar en tierra. A esta hora, ve directo al hotel en un transporte pedido por app o en el puesto oficial. Jamás un taxi de la calle. Te muestro el trayecto cuando quieras."),
        "en": ("Welcome to {ciudad}", "You should be on the ground now. At this hour, go straight to your hotel in a ride booked by app or at the official desk. Never a street taxi. I can show the route anytime."),
        "pt": ("Bem-vindo a {ciudad}", "Você já deve estar em solo. A esta hora, vá direto ao hotel num transporte pedido por app ou no balcão oficial. Jamais um táxi de rua. Mostro o trajeto quando quiser."),
        "fr": ("Bienvenue à {ciudad}", "Tu dois être au sol. À cette heure, va droit à l'hôtel avec un trajet commandé par appli ou au comptoir officiel. Jamais un taxi de rue. Je te montre l'itinéraire quand tu veux."),
        "de": ("Willkommen in {ciudad}", "Du bist jetzt wohl gelandet. Fahr um diese Zeit direkt zum Hotel, mit einer per App bestellten Fahrt oder vom offiziellen Schalter. Nie ein Straßentaxi. Die Route zeige ich dir jederzeit."),
        "it": ("Benvenuto a {ciudad}", "Dovresti essere a terra. A quest'ora vai dritto in hotel con un trasporto prenotato da app o al banco ufficiale. Mai un taxi di strada. Ti mostro il percorso quando vuoi."),
    },
    "noche_plan": {
        "es": ("Plan de esta noche", "Para ir y volver pide tu taxi por app (Uber, inDrive, Cabify). Jamás tomes un taxi de la calle. Lleva solo lo necesario y cuida tu trago. Te muestro el trayecto en Cómo llegar."),
        "en": ("Tonight's plan", "To go and come back, book your taxi in an app (Uber, inDrive, Cabify). Never take a street taxi. Carry only what you need and watch your drink. I can show the route in How to get there."),
        "pt": ("Plano desta noite", "Para ir e voltar, peça seu táxi por app (Uber, inDrive, Cabify). Jamais pegue um táxi na rua. Leve só o necessário e cuide da sua bebida. Mostro o trajeto em Como chegar."),
        "fr": ("Ton plan de ce soir", "Pour y aller et rentrer, commande ton taxi par appli (Uber, inDrive, Cabify). Jamais un taxi dans la rue. Prends le strict nécessaire et surveille ton verre. Je te montre l'itinéraire dans Comment y aller."),
        "de": ("Dein Plan heute Abend", "Bestell für Hin- und Rückweg ein Taxi per App (Uber, inDrive, Cabify). Nie ein Taxi von der Straße. Nimm nur das Nötige mit und pass auf dein Getränk auf. Die Route zeige ich dir unter Wie komme ich hin."),
        "it": ("Il piano di stasera", "Per andare e tornare prenota il taxi con un'app (Uber, inDrive, Cabify). Mai un taxi preso in strada. Porta solo il necessario e controlla il tuo drink. Ti mostro il percorso in Come arrivare."),
    },
    "vuelta_tarde": {
        "es": ("Volver con seguridad", "Si ya es tarde, pide tu taxi por app desde donde estás y espéralo adentro del lugar. Jamás tomes uno de la calle. Abre Cómo llegar y te muestro el trayecto a tu hotel."),
        "en": ("Getting back safely", "If it's late, book your taxi in an app from where you are and wait for it inside. Never take one from the street. Open How to get there and I'll show the route to your hotel."),
        "pt": ("Voltar com segurança", "Se já é tarde, peça seu táxi por app de onde você está e espere lá dentro. Jamais pegue um da rua. Abra Como chegar e mostro o trajeto até o hotel."),
        "fr": ("Rentrer en sécurité", "S'il est tard, commande ton taxi par appli d'où tu es et attends-le à l'intérieur. Jamais un taxi de rue. Ouvre Comment y aller et je te montre le trajet vers l'hôtel."),
        "de": ("Sicher zurück", "Wenn es spät ist, bestell dein Taxi per App von dort, wo du bist, und warte drinnen. Nie eins von der Straße. Öffne Wie komme ich hin, dann zeige ich dir die Route zum Hotel."),
        "it": ("Tornare in sicurezza", "Se è tardi, prenota il taxi con un'app da dove sei e aspettalo dentro il locale. Mai uno preso in strada. Apri Come arrivare e ti mostro il percorso verso l'hotel."),
    },
    "primera_noche": {
        "es": ("Tu primera noche", "¿Ya sabes dónde cenar? Dime qué se te antoja y te digo qué está abierto ahora. Si sales, pide el taxi por app y jamás uno de la calle."),
        "en": ("Your first night", "Know where to eat? Tell me what you're craving and I'll tell you what's open now. If you go out, book your taxi by app and never one from the street."),
        "pt": ("Sua primeira noite", "Já sabe onde jantar? Diga o que você quer e eu falo o que está aberto agora. Se sair, peça o táxi por app e jamais um da rua."),
        "fr": ("Ta première nuit", "Tu sais où dîner ? Dis-moi ce qui te tente et je te dis ce qui est ouvert maintenant. Si tu sors, commande ton taxi par appli et jamais un taxi de rue."),
        "de": ("Deine erste Nacht", "Weißt du schon, wo du isst? Sag mir, worauf du Lust hast, und ich sage dir, was jetzt offen ist. Wenn du ausgehst: Taxi per App, nie eins von der Straße."),
        "it": ("La tua prima notte", "Sai dove cenare? Dimmi cosa ti va e ti dico cosa è aperto adesso. Se esci, prenota il taxi con l'app e mai uno preso in strada."),
    },
}

# Ideas del mediodía (rotan por día). Cada una invita a pedirle algo concreto al Companion.
TIPS: dict[str, list[tuple[str, str]]] = {
    "es": [("Una idea para hoy", "Pregúntame por el mejor ajiaco o las mejores empanadas cerca de donde estás."),
           ("Una idea para hoy", "Dime tu plan de hoy y te digo a qué hora salir para llegar sin trancón."),
           ("Una idea para hoy", "Pídeme un lugar local que casi no conocen los turistas, según tus gustos."),
           ("Una idea para hoy", "Sube tus reservas o escribe tu itinerario y lo ordeno por días."),
           ("Una idea para hoy", "Pregúntame qué hay abierto ahora cerca de ti, a la hora que sea.")],
    "en": [("An idea for today", "Ask me for the best local soup or empanadas near where you are."),
           ("An idea for today", "Tell me today's plan and I'll tell you when to leave to beat the traffic."),
           ("An idea for today", "Ask for a local spot most tourists miss, based on your tastes."),
           ("An idea for today", "Upload your bookings or type your itinerary and I'll organize it by day."),
           ("An idea for today", "Ask me what's open right now near you, at any hour.")],
    "pt": [("Uma ideia para hoje", "Pergunte pela melhor sopa ou empanadas locais perto de onde você está."),
           ("Uma ideia para hoje", "Conte o plano de hoje e eu digo a que horas sair para fugir do trânsito."),
           ("Uma ideia para hoje", "Peça um lugar local que poucos turistas conhecem, conforme seus gostos."),
           ("Uma ideia para hoje", "Envie suas reservas ou escreva o roteiro e eu organizo por dia."),
           ("Uma ideia para hoje", "Pergunte o que está aberto agora perto de você, a qualquer hora.")],
    "fr": [("Une idée pour aujourd'hui", "Demande-moi la meilleure soupe ou les meilleures empanadas près de toi."),
           ("Une idée pour aujourd'hui", "Dis-moi ton plan du jour et je te dis quand partir pour éviter les bouchons."),
           ("Une idée pour aujourd'hui", "Demande un lieu local que peu de touristes connaissent, selon tes goûts."),
           ("Une idée pour aujourd'hui", "Envoie tes réservations ou écris ton itinéraire et je l'organise par jour."),
           ("Une idée pour aujourd'hui", "Demande-moi ce qui est ouvert maintenant près de toi, à toute heure.")],
    "de": [("Eine Idee für heute", "Frag mich nach der besten lokalen Suppe oder den besten Empanadas in deiner Nähe."),
           ("Eine Idee für heute", "Nenn mir deinen Tagesplan und ich sage dir, wann du losmusst, um dem Stau zu entgehen."),
           ("Eine Idee für heute", "Frag nach einem lokalen Ort, den kaum Touristen kennen, nach deinem Geschmack."),
           ("Eine Idee für heute", "Lade deine Buchungen hoch oder schreib deinen Plan, ich ordne ihn nach Tagen."),
           ("Eine Idee für heute", "Frag mich, was jetzt in deiner Nähe offen ist, zu jeder Uhrzeit.")],
    "it": [("Un'idea per oggi", "Chiedimi la migliore zuppa o le migliori empanadas vicino a te."),
           ("Un'idea per oggi", "Dimmi il piano di oggi e ti dico a che ora uscire per evitare il traffico."),
           ("Un'idea per oggi", "Chiedimi un posto locale che pochi turisti conoscono, secondo i tuoi gusti."),
           ("Un'idea per oggi", "Carica le prenotazioni o scrivi l'itinerario e lo organizzo per giorni."),
           ("Un'idea per oggi", "Chiedimi cosa è aperto adesso vicino a te, a qualsiasi ora.")],
}


def _lang(trip: dict) -> str:
    i = (trip.get("idioma") or "es").lower()
    return i if i in TXT["noche_plan"] else "es"


def _hora(plan: dict, fecha: str) -> float | None:
    if plan.get("fecha") != fecha or not plan.get("hora"):
        return None
    try:
        hh, mm = plan["hora"].split(":")[:2]
        return int(hh) + int(mm) / 60
    except (ValueError, AttributeError):
        return None


def _es_noche(h: float) -> bool:
    return h >= LLEGADA_NOCHE[0] or h < LLEGADA_NOCHE[1]


def hora_llegada(trip: dict, planes: list[dict]) -> str | None:
    """Hora de aterrizaje del primer día: la que escribió en «vuelo de ida» o, si no, un plan de vuelo de ese día
    que no sea una salida. None si no se sabe (no se inventa)."""
    ll = reloj.leer_llegada(trip.get("vuelo_ida") or "")
    if ll and not ll.get("ambigua"):
        return ll["hora"]
    for p in planes:
        if p.get("tipo") != "vuelo" or p.get("fecha") != trip.get("inicio") or not p.get("hora"):
            continue
        texto = f"{p.get('titulo') or ''} {p.get('detalle') or ''}".lower()
        if re.search(r"regreso|salida|despega|departure|return", texto):
            continue
        return p["hora"]
    return None


def _plan_nocturno(plan: dict, ciudad: str, fecha: str) -> float | None:
    """Hora del plan si es nocturno (desde las 20:00, de madrugada o un bar/rumba); None si no lo es."""
    h = _hora(plan, fecha)
    if h is None:
        return None
    if h >= HORA_NOCHE or h < 5:
        return h
    lugar = (plan.get("lugar") or plan.get("titulo") or "")
    if _FIESTA.search(f"{plan.get('titulo') or ''} {lugar}"):
        return h
    try:
        m = db.best_place_match(lugar, db.places_for_city(ciudad)) if lugar else None
        if m and m.get("category") == "bar":
            return h
    except Exception:
        pass
    return None


def _enviar(trip: dict, kind: str, fecha: str, titulo: str, cuerpo: str) -> bool:
    """Marca y envía (push + mensaje en el chat). Devuelve True si salió."""
    tid = trip["id"]
    if not db.mark_proactive_sent(tid, kind, fecha):
        return False
    try:
        db.insert("messages", {"trip_id": tid, "role": "companion", "content": cuerpo})
        from . import push
        push.send_to_trip_async(tid, titulo, cuerpo, data={"kind": kind, "open": "chat"})
    except Exception:
        log.exception("consejo_fallo kind=%s trip=%s", kind, tid)
        try:
            db.unmark_proactive_sent(tid, kind, fecha)
        except Exception:
            pass
        return False
    return True


def _texto(kind: str, trip: dict) -> tuple[str, str]:
    t, b = TXT[kind][_lang(trip)]
    ciudad = trip.get("ciudad") or ""
    return t.format(ciudad=ciudad), b.format(ciudad=ciudad)


def revisar(trip: dict, ahora: datetime, planes: list[dict] | None = None) -> list[str]:
    """Todo lo que toque avisar a este viaje en este momento. Devuelve los kinds enviados. Nunca lanza."""
    enviados: list[str] = []
    try:
        if planes is None:
            planes = db.get_plans(trip["id"])
        hoy = ahora.date().isoformat()
        h = ahora.hour + ahora.minute / 60
        ciudad = trip.get("ciudad", "")

        # ── Llegada (solo el primer día del viaje) ──
        aun_en_vuelo = False
        if hoy == trip.get("inicio"):
            hl = hora_llegada(trip, planes)
            if hl:
                hh, mm = (int(x) for x in hl.split(":"))
                aterriza = ahora.replace(hour=hh, minute=mm, second=0, microsecond=0)
                noche = _es_noche(hh + mm / 60)
                falta = (aterriza - ahora).total_seconds() / 60
                aun_en_vuelo = falta > 0
                if 20 <= falta <= 90:
                    k = "llegada_pre_noche" if noche else "llegada_pre_dia"
                    if _enviar(trip, "consejo_" + k, hoy, *_texto(k, trip)):
                        enviados.append(k)
                if 10 <= -falta <= 120:
                    k = "aterrizaje_noche" if noche else "aterrizaje_dia"
                    if _enviar(trip, "consejo_" + k, hoy, *_texto(k, trip)):
                        enviados.append(k)

        # ── Noche: planes nocturnos de hoy ──
        nocturnos = [x for x in (_plan_nocturno(p, ciudad, hoy) for p in planes) if x is not None]
        if nocturnos:
            tarde = [x for x in nocturnos if x >= 5]
            antes = (min(tarde) - h) * 60 if tarde else None
            if antes is not None and 30 <= antes <= 120:
                if _enviar(trip, "consejo_noche_plan", hoy, *_texto("noche_plan", trip)):
                    enviados.append("noche_plan")
            if h >= 22.5 or h < 4:
                if _enviar(trip, "consejo_vuelta_tarde", hoy, *_texto("vuelta_tarde", trip)):
                    enviados.append("vuelta_tarde")
        elif 21.0 <= h < 22.5 and hoy == trip.get("inicio") and not aun_en_vuelo:
            if _enviar(trip, "consejo_primera_noche", hoy, *_texto("primera_noche", trip)):
                enviados.append("primera_noche")

        # ── Idea del mediodía, si todavía no abrió la app ──
        if 11.0 <= h < 12.5 and not db.user_active_within(trip["id"], 180):
            try:
                dia = (ahora.date() - datetime.strptime(trip["inicio"], "%Y-%m-%d").date()).days
            except (KeyError, ValueError):
                dia = 0
            tips = TIPS[_lang(trip)]
            t, b = tips[dia % len(tips)]
            if _enviar(trip, "consejo_tip_dia", hoy, t, b):
                enviados.append("tip_dia")
    except Exception:
        log.exception("consejos_error trip=%s", trip.get("id"))
    return enviados
