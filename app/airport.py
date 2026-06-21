"""Modo Aeropuerto del Companion — el copiloto del momento de llegada.

Cuando el sistema detecta que el usuario ACABA DE ATERRIZAR (zona_actual ==
"aeropuerto"), la app entra en "modo aeropuerto": en vez del stream normal,
muestra un TIMELINE DE LLEGADA paso a paso (migración → equipaje → salida) y,
al final, opciones de transporte con tips anti-estafa muy concretos del
destino. El objetivo es que el usuario NO se sienta perdido al llegar.

Este módulo es la CURACIÓN por aeropuerto: contenido verificado, redactado para
el usuario, con su fuente. Los TIEMPOS de migración/equipaje son estimados
curados que `watchers.airport_live_times` refresca con búsqueda en vivo
(Tavily) cuando hay key — igual que el resto del pipeline, sin búsqueda cae
limpio al estimado curado.

Hoy: El Dorado (Bogotá, BOG) y Guadalajara (GDL). Agregar un aeropuerto = un
dict nuevo en AIRPORTS + mapearlo en airport_for_city().

FUENTES (jun. 2026): sitio oficial eldorado.aero (migración, taxis, tiempo en
filas en vivo), ANI / Min. Transporte (modernización migratoria 2026),
El Espectador y ASOPARTES (sistema de tiquete de preliquidación de Taxi
Imperial y advertencia sobre "gansos"), guías de transporte locales.
"""
from . import db, search

# Etiquetas de zona que cuentan como "el usuario está en el aeropuerto".
# Se comparan normalizadas (sin tildes, minúsculas) contra zona_actual.
AIRPORT_ZONE_LABELS = {"aeropuerto", "el dorado", "aeropuerto el dorado",
                       "aeropuerto gdl", "gdl", "miguel hidalgo"}


# ── Curación por aeropuerto ────────────────────────────────────────────────
# Cada aeropuerto: pasos del timeline de llegada + opciones de transporte.
# Cada paso trae un estimado de tiempo (rango típico) que la búsqueda en vivo
# puede sobreescribir, un "qué hacer" claro, y tips puntuales.
AIRPORTS = {
    "bog": {
        "code": "BOG",
        "name": "Aeropuerto Internacional El Dorado",
        "city": "Bogotá",
        "official_site": "https://eldorado.aero",
        "live_times_url": "https://eldorado.aero/informacion-en-tiempo-real/tiempo-en-filas",
        "emergency": "123 (línea única de emergencias en Colombia)",
        # Texto compacto para el SYSTEM PROMPT del chat (modo aeropuerto). Mantiene
        # el chat city-aware sin duplicar lógica: context.py lo inyecta tal cual.
        "prompt_block": (
            "\n>>> EL USUARIO ACABA DE ATERRIZAR Y ESTÁ EN EL AEROPUERTO EL DORADO (BOGOTÁ) AHORA MISMO. <<<\n"
            "Prioriza ayudarlo a salir bien: control migratorio, equipaje y, sobre todo, cómo tomar "
            "transporte SEGURO. Si pregunta por taxi/transporte, recuérdale tomar SOLO el taxi oficial "
            "(Taxi Imperial, carril 1 entre puertas 8-10 del piso 1, con tiquete de pre-liquidación) o "
            "pedir una app (Uber/Cabify/inDrive/DiDi) desde la zona de pick-up autorizado; NUNCA aceptar "
            "a quien lo aborde dentro de la terminal (los 'gansos'/piratas). Tono tranquilizador: que no "
            "se sienta perdido.\n"
        ),
        # Pasos del timeline de llegada internacional, en orden.
        "steps": [
            {
                "id": "migracion",
                "titulo": "Control migratorio",
                "icono": "passport",
                "estimado_min": [20, 45],
                "estimado_fuente": "curado",
                "que_hacer": (
                    "Ten listo el pasaporte (y visa si aplica) antes de la fila. "
                    "El Dorado renovó esta zona en 2026: hay 62 módulos, 33 mostradores "
                    "y 29 puntos Biomig."
                ),
                "tips": [
                    "Si tienes pasaporte con chip, busca los puntos Biomig (reconocimiento facial/iris): es mucho más rápido que la fila tradicional.",
                    "Hay filas prioritarias para familias con niños y personas con discapacidad.",
                    "Ten a mano la dirección de tu hotel: a veces la piden en el formulario de ingreso.",
                ],
            },
            {
                "id": "equipaje",
                "titulo": "Recoger equipaje",
                "icono": "luggage",
                "estimado_min": [15, 30],
                "estimado_fuente": "curado",
                "que_hacer": (
                    "Mira las pantallas: te dicen en qué banda sale el equipaje de tu vuelo. "
                    "Las maletas suelen empezar a salir 15-20 min después de aterrizar."
                ),
                "tips": [
                    "Si tu maleta no aparece, reclama en la oficina de tu aerolínea ANTES de salir de la zona de equipajes — después es más difícil.",
                    "Los carritos para maletas son gratuitos en llegadas internacionales.",
                    "Guarda el sticker de tu maleta: lo pueden pedir a la salida.",
                ],
            },
            {
                "id": "aduana",
                "titulo": "Aduana",
                "icono": "customs",
                "estimado_min": [5, 15],
                "estimado_fuente": "curado",
                "que_hacer": (
                    "Pasa por el control de aduana. Si no traes nada que declarar, "
                    "es por el carril verde y suele ser rápido."
                ),
                "tips": [
                    "Declara montos en efectivo superiores a USD 10.000 (o equivalente): no hacerlo es delito.",
                    "Productos agrícolas, carnes y semillas pueden ser retenidos por el ICA.",
                ],
            },
            {
                "id": "salida",
                "titulo": "Salir y tomar transporte",
                "icono": "exit",
                "estimado_min": None,
                "estimado_fuente": "curado",
                "que_hacer": (
                    "Sales al hall de llegadas (piso 1). Aquí es donde más cuidado hay que "
                    "tener: ignora a quien te ofrezca taxi adentro o te jale la maleta. "
                    "Camina a las paradas oficiales — te muestro las opciones abajo."
                ),
                "tips": [
                    "REGLA DE ORO: nunca aceptes transporte de alguien que te aborda dentro de la terminal. Son los llamados 'gansos' (piratas) y son el riesgo #1 al llegar.",
                    "El personal oficial usa uniforme o chaleco distintivo y está entre las puertas 7 y 10 del piso 1.",
                ],
            },
        ],
        # Opciones de transporte para salir del aeropuerto, ordenadas por
        # recomendación para alguien que acaba de llegar (más seguro primero).
        "transporte": [
            {
                "id": "taxi_oficial",
                "nombre": "Taxi oficial (Taxi Imperial)",
                "recomendado": True,
                "como": (
                    "Es la única empresa de taxis autorizada por el aeropuerto (concesión de OPAIN). "
                    "Ve al carril 1, entre las puertas 8 y 10 del piso 1. El personal de chaleco te ayuda "
                    "a registrar tu destino en una tableta y te entrega un TIQUETE con el precio ya "
                    "calculado (pre-liquidación): se lo das al conductor y pagas exactamente eso."
                ),
                "precio_aprox": "Centro/Chapinero ~$35.000–55.000 COP · Norte (Usaquén) ~$45.000–65.000 COP (con recargos según hora y tráfico)",
                "seguridad": "alta",
                "tip": "Pide siempre el tiquete de pre-liquidación: con él no hay sorpresas de tarifa. Tienen datáfono, puedes pagar con tarjeta.",
            },
            {
                "id": "apps",
                "nombre": "App (Uber / Cabify / inDrive / DiDi)",
                "recomendado": True,
                "como": (
                    "Funcionan bien en Bogotá y suelen ser un poco más baratas que el taxi. "
                    "OJO: las apps NO recogen en el hall de llegadas. Tienes que caminar a la zona de "
                    "'Pick-up autorizado' o subir al piso 2 (zona de salidas) y pedir el carro ahí."
                ),
                "precio_aprox": "Centro/Chapinero ~$30.000–50.000 COP · Norte ~$40.000–60.000 COP",
                "seguridad": "alta",
                "tip": "Confirma placa y nombre del conductor antes de subir. De noche o si llegas cansado, el taxi oficial con tiquete es menos estrés.",
            },
            {
                "id": "transmilenio",
                "nombre": "TransMilenio / alimentador (lo más barato)",
                "recomendado": False,
                "como": (
                    "Toma el alimentador gratuito (bus verde) dentro del aeropuerto hacia el Portal El Dorado, "
                    "y de ahí conectas con las troncales. Hacia el centro: buses por la Calle 26."
                ),
                "precio_aprox": "~$3.200 COP (pasaje TransMilenio)",
                "seguridad": "media",
                "tip": "Ideal si viajas ligero y de día. Evítalo de noche o con mucho equipaje: va lleno y es menos cómodo con maletas. TransMilenio cierra ~10–11pm.",
            },
        ],
    },
    "gdl": {
        "code": "GDL",
        "name": "Aeropuerto Internacional de Guadalajara (Miguel Hidalgo y Costilla)",
        "city": "Guadalajara",
        "official_site": "https://www.aeropuertosgap.com.mx/es/guadalajara.html",
        "live_times_url": "",
        "emergency": "911 (línea única de emergencias en México) · Asistencia al turista 078",
        "prompt_block": (
            "\n>>> EL USUARIO ACABA DE ATERRIZAR Y ESTÁ EN EL AEROPUERTO DE GUADALAJARA (GDL) AHORA MISMO. <<<\n"
            "Prioriza ayudarlo a salir bien: control migratorio, equipaje, aduana (semáforo fiscal) y, "
            "sobre todo, cómo tomar transporte SEGURO. DATO CLAVE Y ACTUAL (junio 2026): en GDL las apps "
            "(Uber/DiDi/InDrive) NO pueden recoger dentro de la terminal — es zona federal. El proceso "
            "correcto es: 1) pedir el viaje en la app YA con viaje activo; 2) ir al área de SHUTTLES en "
            "llegadas y buscar la señalización 'C-01 Plataformas'; 3) tomar el shuttle GRATIS que lleva al "
            "patio/estacionamiento de plataformas (sobre carretera a Chapala), donde el conductor recoge. "
            "La otra opción segura es el TAXI AUTORIZADO del aeropuerto (concesionado), que sí recoge en la "
            "terminal: contrátalo en los mostradores/fila oficial de taxis, con tarifa por zona. NUNCA "
            "aceptes a quien te ofrezca taxi abordándote dentro de la terminal. Recuérdale que es ~20–30 "
            "min al Centro/Americana. Tono tranquilizador: que no se sienta perdido.\n"
        ),
        "steps": [
            {
                "id": "migracion",
                "titulo": "Control migratorio",
                "icono": "passport",
                "estimado_min": [20, 50],
                "estimado_fuente": "curado",
                "que_hacer": (
                    "Ten listo el pasaporte (y visa si aplica) antes de la fila. México usa registro "
                    "migratorio electrónico: ya no se llena el papelito FMM en la mayoría de los casos, "
                    "pero el oficial puede preguntarte motivo del viaje, cuántos días te quedas y dónde "
                    "te alojas."
                ),
                "tips": [
                    "Ten a mano la dirección de tu hotel: a veces la piden.",
                    "Las filas pueden crecer cuando aterrizan varios vuelos juntos; si ves dos filas (mexicanos/extranjeros), ponte en la de extranjeros.",
                    "Guarda cualquier sello o comprobante de entrada que te den: es tu constancia de estancia legal.",
                ],
            },
            {
                "id": "equipaje",
                "titulo": "Recoger equipaje",
                "icono": "luggage",
                "estimado_min": [15, 30],
                "estimado_fuente": "curado",
                "que_hacer": (
                    "Mira las pantallas: te dicen en qué banda sale el equipaje de tu vuelo. "
                    "Las maletas suelen empezar a salir 15-20 min después de aterrizar."
                ),
                "tips": [
                    "Si tu maleta no aparece, reclama en el mostrador de tu aerolínea ANTES de salir de la zona de equipajes.",
                    "Hay carritos para maletas; revisa si tienen costo.",
                    "Conserva el sticker de tu maleta: lo pueden cotejar a la salida.",
                ],
            },
            {
                "id": "aduana",
                "titulo": "Aduana (semáforo fiscal)",
                "icono": "customs",
                "estimado_min": [5, 15],
                "estimado_fuente": "curado",
                "que_hacer": (
                    "Al salir, presiona el botón del semáforo fiscal: si sale VERDE, pasas directo; "
                    "si sale ROJO, te revisan el equipaje (es aleatorio, no te preocupes). Entrega tu "
                    "declaración de aduana si la pidieron en el vuelo."
                ),
                "tips": [
                    "Declara montos en efectivo superiores a USD 10.000 (o equivalente): no hacerlo es delito.",
                    "Hay límites de 'franquicia' para lo que traes; productos agrícolas, carnes y semillas pueden ser retenidos.",
                ],
            },
            {
                "id": "salida",
                "titulo": "Salir y tomar transporte",
                "icono": "exit",
                "estimado_min": None,
                "estimado_fuente": "curado",
                "que_hacer": (
                    "Sales al hall de llegadas. Aquí hay que tener cuidado: ignora a quien te ofrezca "
                    "taxi o te jale la maleta. Para app (Uber/DiDi/InDrive), ve al área de Shuttles y "
                    "busca la señal 'C-01 Plataformas' para el shuttle gratis al patio de plataformas. "
                    "Para taxi, usa SOLO la fila/mostrador del taxi autorizado del aeropuerto."
                ),
                "tips": [
                    "REGLA DE ORO: no aceptes transporte de quien te aborde dentro de la terminal. Usa solo el taxi autorizado en su mostrador, o la app vía el shuttle C-01.",
                    "Para la app: ten el viaje ya solicitado y activo ANTES de buscar el shuttle; te pueden pedir mostrarlo.",
                    "El tren ligero no llega al aeropuerto, pero hay bus/shuttle que conecta con la estación Periférico Norte de la L1.",
                ],
            },
        ],
        "transporte": [
            {
                "id": "taxi_oficial",
                "nombre": "Taxi autorizado del aeropuerto",
                "recomendado": True,
                "como": (
                    "Es el único taxi que puede recogerte DENTRO de la terminal (servicio concesionado). "
                    "Búscalo en los mostradores / fila oficial de taxis en llegadas; te cobran por zona "
                    "según tu destino. Pídelo ahí, nunca con alguien que te aborde caminando."
                ),
                "precio_aprox": "Centro/Americana/Minerva ~$350–550 MXN según zona y tráfico",
                "seguridad": "alta",
                "tip": "Confirma la tarifa por zona antes de subir. Es la opción más simple si llegas de noche o cansado, aunque suele ser más cara que la app.",
            },
            {
                "id": "apps",
                "nombre": "App (Uber / DiDi / InDrive) vía shuttle C-01",
                "recomendado": True,
                "como": (
                    "Las apps NO recogen dentro de la terminal (zona federal). Proceso (vigente desde "
                    "jun. 2026): pide el viaje en la app, ve al área de Shuttles en llegadas, busca la "
                    "señalización 'C-01 Plataformas' y toma el shuttle GRATIS que te lleva al patio de "
                    "plataformas (sobre carretera a Chapala), donde te recoge tu conductor."
                ),
                "precio_aprox": "Centro/Americana ~$250–380 MXN (suele ser más barato que el taxi)",
                "seguridad": "alta",
                "tip": "Ten el viaje ya solicitado y activo antes de buscar el shuttle. Verifica placa y modelo del auto antes de subir.",
            },
            {
                "id": "tren_bus",
                "nombre": "Bus + Tren Ligero (lo más barato)",
                "recomendado": False,
                "como": (
                    "Hay un servicio de bus/shuttle que conecta el aeropuerto con la estación Periférico "
                    "Norte de la Línea 1 del tren ligero; de ahí cruzas la ciudad en tren. Económico pero "
                    "más lento y con transbordo."
                ),
                "precio_aprox": "~$9.50 MXN el tren + costo del bus (muy económico)",
                "seguridad": "media",
                "tip": "Conviene solo si viajas ligero y de día. Con maletas o de noche, mejor app o taxi autorizado.",
            },
        ],
    },
}


def norm(s: str) -> str:
    import unicodedata
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return s.strip().lower()


def is_airport_zone(zona_actual: str) -> bool:
    """¿La zona actual del usuario significa que está en el aeropuerto?"""
    z = norm(zona_actual)
    return any(label in z or z in label for label in AIRPORT_ZONE_LABELS)


def airport_for_city(city: str) -> dict | None:
    """Devuelve la curación del aeropuerto principal de una ciudad, o None."""
    c = norm(city)
    if "bogota" in c:
        return AIRPORTS["bog"]
    if "guadalajara" in c:
        return AIRPORTS["gdl"]
    return None


def live_times(airport: dict) -> dict[str, list[int] | None]:
    """Intenta refrescar los tiempos de migración/equipaje con búsqueda en vivo.

    Devuelve {step_id: [min, max] | None}. Sin TAVILY_API_KEY o si la búsqueda
    no arroja un número claro, devuelve {} y el caller se queda con el estimado
    curado. La búsqueda nunca debe romper el flujo de llegada.
    """
    if search.MODE == "mock":
        return {}
    try:
        resultados = search.search(
            f"{airport['name']} tiempo espera migración filas hoy minutos",
            max_results=3, topic="news",
        )
    except Exception:
        return {}

    # Heurística simple y conservadora: si algún resultado menciona un número
    # de minutos junto a "migrac"/"fila", lo usamos como tope superior del rango
    # de migración. No inventamos; si no hay señal clara, no tocamos nada.
    import re
    for item in resultados:
        texto = norm(f"{item.get('title','')} {item.get('content','')}")
        if "migrac" not in texto and "fila" not in texto:
            continue
        m = re.search(r"(\d{1,3})\s*minut", texto)
        if m:
            mins = int(m.group(1))
            if 5 <= mins <= 180:
                return {"migracion": [max(10, mins - 15), mins]}
    return {}


def arrival_payload(trip: dict, with_live: bool = True) -> dict:
    """Arma la respuesta del modo aeropuerto para un trip.

    Combina la curación con los tiempos en vivo (si los hay) y devuelve una
    estructura lista para que el frontend pinte el timeline + transporte.
    """
    airport = airport_for_city(trip["ciudad"])
    if not airport:
        return {"disponible": False, "ciudad": trip["ciudad"],
                "nota": f"Todavía no tengo el modo aeropuerto curado para {trip['ciudad']}."}

    overrides = live_times(airport) if with_live else {}
    fuente_tiempos = "en_vivo" if overrides else ("curado" if search.MODE == "mock" else "curado")

    pasos = []
    for s in airport["steps"]:
        est = overrides.get(s["id"], s["estimado_min"])
        pasos.append({
            "id": s["id"],
            "titulo": s["titulo"],
            "icono": s["icono"],
            "estimado_min": est,
            "estimado_fuente": "en_vivo" if s["id"] in overrides else s["estimado_fuente"],
            "que_hacer": s["que_hacer"],
            "tips": s["tips"],
        })

    total = sum((p["estimado_min"][1] for p in pasos if p["estimado_min"]), 0)

    return {
        "disponible": True,
        "code": airport["code"],
        "name": airport["name"],
        "city": airport["city"],
        "official_site": airport["official_site"],
        "live_times_url": airport["live_times_url"],
        "emergency": airport["emergency"],
        "tiempo_total_estimado_min": total,
        "tiempos_modo": fuente_tiempos,
        "pasos": pasos,
        "transporte": airport["transporte"],
        "destino_sugerido": trip.get("hotel", ""),
    }
