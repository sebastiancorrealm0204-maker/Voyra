"""Guía de ciudad estructurada → el conocimiento local del Companion.

El problema que resuelve: los 77 lugares curados (seed_data.py) le dicen al
Companion DÓNDE comer, pero no cómo FUNCIONA la ciudad. Sin esto, el modelo
improvisa transporte, zonas, seguridad y costumbres desde su memoria vaga, y
suena a turista perdido en vez de a un local que conoce el terreno.

Este módulo es la contraparte de seed_data: conocimiento verificado y estable
(no cambia con el GPS) que entra al system prompt para que el Companion hable
con autoridad real sobre la ciudad. Mismo principio anti-alucinación: lo que
está aquí es lo que el Companion sabe; fuera de aquí, que sea honesto.

Estructura por ciudad (todos los campos son texto plano, listos para el prompt):
- resumen:     1-2 frases de orientación general (altura, clima, carácter).
- transporte:  cómo moverse de verdad — apps, taxis, TransMilenio, rangos de precio.
- zonas:       qué es cada barrio y para qué sirve (dónde quedarse, comer, salir).
- seguridad:   reglas concretas por zona y hora, sin alarmismo.
- dinero:      moneda, efectivo vs tarjeta, propinas, cajeros.
- clima:       qué esperar y qué llevar (Bogotá no tiene estaciones, tiene horas).
- util:        datos sueltos de alto valor (altura, agua, enchufes, emergencias).
"""
from .db import norm_city


# ─────────────────────────────────────────────────────────────────────────────
# BOGOTÁ — curada a fondo (junio 2026). Fuente: conocimiento local verificado.
# ─────────────────────────────────────────────────────────────────────────────
_BOGOTA = {
    "display": "Bogotá",
    "resumen": (
        "Bogotá está a 2.640 m de altura — el aire es más delgado y el sol pega fuerte "
        "aunque haga frío. Clima de montaña: entre 7°C y 19°C casi todo el año, sin "
        "estaciones; cambia varias veces en un mismo día. La ciudad se organiza de sur "
        "(más popular) a norte (más pudiente). Las direcciones son lógicas: Calles van "
        "de oriente a occidente y suben de número hacia el norte; Carreras van de sur a "
        "norte y suben hacia el occidente. Los cerros (Monserrate) siempre quedan al "
        "ORIENTE — son tu brújula: si los ves a tu derecha, vas hacia el norte."
    ),
    "transporte": (
        "MOVERSE EN BOGOTÁ:\n"
        "• Apps de carro (lo más seguro y lo que usa un local de noche): Uber, DiDi, "
        "Cabify e inDrive funcionan bien. Uber opera en zona gris legal pero es de uso "
        "masivo y seguro; pídelo siempre por app, nunca en la calle.\n"
        "• Taxi amarillo: legal y barato, pero pídelo SIEMPRE por app (Tappsi/Cabify o "
        "el de la app de taxi) o que lo pida el restaurante/hotel — nunca pares uno en "
        "la calle de noche. Exige que prenda el taxímetro. Carrera corta dentro del "
        "norte: ~$8.000–15.000 COP. Del aeropuerto al norte: ~$35.000–45.000 por "
        "taxímetro + recargos.\n"
        "• TransMilenio (bus articulado por carril exclusivo): rápido para distancias "
        "largas norte-sur, ~$3.200 COP el pasaje con tarjeta TuLlave. Pero va MUY lleno "
        "en hora pico (6-8am, 5-7pm) y es el punto caliente de carterismo — no lo "
        "recomiendo para un viajero cargando cosas o de noche.\n"
        "• SITP (buses azules normales): complementan, misma tarjeta TuLlave.\n"
        "• Caminar: el norte (Zona G, Zona T, Parque 93, Usaquén) es caminable y "
        "agradable de día. La Candelaria se camina de día; de noche, carro.\n"
        "• Bici: Bogotá tiene buenas ciclorrutas y los domingos la Ciclovía cierra vías "
        "principales para bicis y corredores (7am-2pm) — vale la pena vivirla.\n"
        "• Regla de oro: para ir del sur al norte o cruzar la ciudad en hora pico, "
        "calcula el doble de tiempo. El tráfico de Bogotá es pesado."
    ),
    "zonas": (
        "ZONAS CLAVE (de norte a sur):\n"
        "• Usaquén: norte, ambiente de pueblo dentro de la ciudad. Mercado de pulgas los "
        "domingos, brunch, restaurantes. Tranquilo y seguro.\n"
        "• Parque de la 93 / Chicó: norte pudiente, restaurantes y bares alrededor de un "
        "parque elegante. Seguro, ideal para comer y tomar algo.\n"
        "• Zona T y Zona Rosa: corazón de la rumba y las compras del norte. Bares, "
        "discotecas, restaurantes. Animado de noche, relativamente seguro pero con "
        "cuidado normal de zona de fiesta.\n"
        "• Zona G ('G' de gourmet): epicentro gastronómico, en Chapinero Alto cerca de "
        "la Calle 70. Los mejores restaurantes de la ciudad.\n"
        "• Chapinero: amplio y diverso. Chapinero Alto (hacia los cerros) es bueno; "
        "Chapinero centro es más popular y conviene cuidado de noche.\n"
        "• La Candelaria (centro histórico): casas coloniales, museos (Botero, Oro), "
        "Plaza de Bolívar, universidades. Imperdible de DÍA. De noche pierde gente y "
        "conviene moverse en carro y no andar mostrando cosas.\n"
        "• Teusaquillo / Galerías: residencial, bohemio, buen valor.\n"
        "Para quedarse: el norte (Usaquén, Chicó, Zona G/T, Chapinero Alto) es la apuesta "
        "más cómoda y segura para un viajero."
    ),
    "seguridad": (
        "SEGURIDAD (realista, sin alarmismo):\n"
        "• 'No dar papaya' es la regla local: no exhibas celular, joyas ni efectivo en la "
        "calle. La mayoría de incidentes son hurtos de oportunidad, no violencia.\n"
        "• Celular: úsalo pegado a una pared o dentro de un local, no caminando por el "
        "borde del andén ni en un semáforo (raponazo en moto). En TransMilenio, guárdalo.\n"
        "• De noche: muévete en Uber/taxi por app, no caminando, sobre todo fuera del "
        "norte. La Candelaria y el centro de noche: en carro.\n"
        "• Taxis en la calle: el riesgo del 'paseo millonario' existe — por eso SIEMPRE "
        "por app. Si pides app de carro, verifica placa y modelo antes de subir.\n"
        "• Cuidado con bebidas: no dejes el trago solo en bares; la escopolamina existe.\n"
        "• Zonas turísticas de día (norte, Candelaria, Monserrate): tranquilas con "
        "sentido común. Emergencias: 123."
    ),
    "dinero": (
        "DINERO:\n"
        "• Moneda: peso colombiano (COP). A junio 2026, ~4.000 COP por dólar (varía).\n"
        "• Tarjeta vs efectivo: tarjeta se acepta en restaurantes, hoteles y tiendas del "
        "norte sin problema. Lleva algo de efectivo para taxis, mercados, propinas y "
        "lugares pequeños.\n"
        "• Cajeros: usa los que estén DENTRO de bancos, centros comerciales o locales, no "
        "los de la calle, y de día. Avísale a tu banco que viajas.\n"
        "• Propina: en restaurantes la 'propina voluntaria' del 10% suele venir sugerida "
        "en la cuenta — te preguntan si la incluyes, y es costumbre aceptarla si "
        "atendieron bien. Taxis no esperan propina.\n"
        "• Datáfono: te lo traen a la mesa; la tarjeta nunca debe salir de tu vista."
    ),
    "clima": (
        "CLIMA Y QUÉ LLEVAR:\n"
        "• Bogotá no tiene verano/invierno de temperatura, tiene horas: mañanas frías, "
        "mediodías templados con sol fuerte (por la altura), tardes que pueden llover, "
        "noches frías (~8-10°C).\n"
        "• Vístete en capas: camiseta + algo abrigado + una chaqueta impermeable o "
        "paraguas pequeño SIEMPRE. La lluvia llega de repente.\n"
        "• El sol de altura quema aunque haga frío: bloqueador aunque esté nublado.\n"
        "• Temporada más lluviosa: abril-mayo y octubre-noviembre. El resto llueve menos "
        "pero nunca confíes — lleva el paraguas."
    ),
    "util": (
        "DATOS ÚTILES:\n"
        "• Altura (2.640 m): el primer día puede dar leve dolor de cabeza o cansancio. "
        "Toma agua, ve con calma, modera el alcohol la primera noche.\n"
        "• Agua: la del grifo en Bogotá es potable y de buena calidad — puedes tomarla.\n"
        "• Enchufes: tipo A/B, 110V (igual que EE.UU.). \n"
        "• Emergencias: 123 (línea única). \n"
        "• Idioma: español; en zonas turísticas y hoteles del norte hay algo de inglés, "
        "pero fuera de ahí poco — el Companion ayuda con eso.\n"
        "• 'Tinto' = café negro pequeño, no vino. Te lo ofrecen en todas partes."
    ),
}


# ─────────────────────────────────────────────────────────────────────────────
# CARTAGENA — esqueleto inicial (ampliar con la misma profundidad que Bogotá).
# ─────────────────────────────────────────────────────────────────────────────
_CARTAGENA = {
    "display": "Cartagena",
    "resumen": (
        "Cartagena es caribe a nivel del mar: calor y humedad altos todo el año "
        "(28-32°C), sol fuerte. El corazón turístico es la Ciudad Amurallada (centro "
        "histórico colonial) y Getsemaní, ambos caminables. Bocagrande es la franja "
        "moderna de hoteles frente al mar."
    ),
    "transporte": (
        "MOVERSE EN CARTAGENA:\n"
        "• El centro histórico y Getsemaní se caminan — son pequeños y es la mejor forma.\n"
        "• Apps: Uber e inDrive funcionan; pídelas por app, no en la calle.\n"
        "• Taxis no tienen taxímetro: se acuerda el precio ANTES de subir. Trayecto "
        "dentro de la zona turística: ~$10.000–20.000 COP. Pregunta a tu hotel el rango "
        "justo para no pagar de más.\n"
        "• Del aeropuerto (Rafael Núñez) al centro: ~10-15 min, ~$15.000–25.000 COP "
        "acordado antes."
    ),
    "zonas": (
        "ZONAS:\n"
        "• Ciudad Amurallada (centro histórico): el casco colonial dentro de las "
        "murallas. Plazas, balcones, restaurantes, el imperdible. Caminable y seguro.\n"
        "• Getsemaní: junto al centro, antes bohemio y hoy muy de moda — street art, "
        "vida nocturna, hostales y buena comida. Caminable.\n"
        "• Bocagrande: franja moderna de hoteles altos y playa urbana. Cómodo pero "
        "menos encanto colonial.\n"
        "• Islas (Rosario, Barú): excursión de día en lancha para playa de verdad."
    ),
    "seguridad": (
        "SEGURIDAD:\n"
        "• Zona turística tranquila con sentido común. Vendedores ambulantes insistentes "
        "en plazas y playa: un 'no, gracias' firme basta.\n"
        "• Acuerda precios antes (taxis, lanchas, tours) para evitar sobrecobros.\n"
        "• De noche el centro y Getsemaní tienen movimiento; cuidado normal. "
        "Emergencias: 123."
    ),
    "dinero": (
        "DINERO:\n"
        "• Peso colombiano (COP). Tarjeta en hoteles y restaurantes; efectivo para "
        "taxis, playa, mercados y propinas.\n"
        "• Propina voluntaria 10% sugerida en restaurantes."
    ),
    "clima": (
        "CLIMA Y QUÉ LLEVAR:\n"
        "• Calor y humedad constantes: ropa ligera, bloqueador, sombrero, mucha agua.\n"
        "• Lluvias breves e intensas sobre todo agosto-noviembre.\n"
        "• El sol del Caribe es intenso: hidrátate y busca sombra al mediodía."
    ),
    "util": (
        "DATOS ÚTILES:\n"
        "• Agua: mejor embotellada.\n"
        "• Enchufes tipo A/B, 110V.\n"
        "• Emergencias: 123."
    ),
}


CITY_GUIDES: dict[str, dict] = {
    "bogota": _BOGOTA,
    "cartagena": _CARTAGENA,
    "guadalajara": None,  # se asigna abajo tras definir _GUADALAJARA
}


# ─────────────────────────────────────────────────────────────────────────────
# GUADALAJARA — curada (junio 2026). Fuente: conocimiento local verificado.
# ─────────────────────────────────────────────────────────────────────────────
_GUADALAJARA = {
    "display": "Guadalajara",
    "resumen": (
        "Guadalajara es la capital de Jalisco, en el occidente de México, a ~1.560 m de "
        "altura — clima templado y seco casi todo el año, no es altura que marée. Cuna del "
        "tequila, el mariachi y la birria. La zona metropolitana incluve Zapopan (moderno, "
        "Andares, la Basílica), Tlaquepaque y Tonalá (artesanías), y el corazón es el Centro "
        "Histórico con la Catedral. Tequila (el pueblo) queda a ~60 km al oeste."
    ),
    "transporte": (
        "MOVERSE EN GUADALAJARA:\n"
        "• Apps de carro (lo más práctico y seguro): Uber, DiDi e inDriver funcionan muy bien "
        "y suelen ser más baratas que el taxi. Pídelas por app, nunca pares un taxi en la calle.\n"
        "• Taxis: si necesitas uno, que el hotel llame a un 'sitio' (taxi regulado). Muchos no "
        "usan taxímetro: acuerda el precio ANTES de subir.\n"
        "• Mi Transporte (sistema público SITEUR): Mi Tren (tren ligero, 3 líneas), Mi Macro / "
        "Macrobús (buses troncales por carril exclusivo) y SITREN. Económico y eficiente para "
        "cruzar la ciudad; hay pases de 1, 3 o 7 días desde ~$108 MXN.\n"
        "• MiBici: bicis públicas, ideales en Chapultepec, la Americana y el Centro.\n"
        "• Desde el aeropuerto (GDL): Uber/DiDi tienen zonas de abordaje específicas (revisa la "
        "app el día del vuelo); en taxi/app al centro son ~20–30 min. El bus Ruta 300 conecta "
        "con la ciudad en ~40 min.\n"
        "• A pie: el Centro Histórico, Chapultepec y la Americana son caminables y agradables de día."
    ),
    "zonas": (
        "ZONAS CLAVE:\n"
        "• Centro Histórico: la Catedral, Plaza de Armas, Teatro Degollado, el Hospicio "
        "Cabañas (murales de Orozco). Imperdible de día.\n"
        "• Chapultepec / Lafayette (Colonia Americana): el corazón cool — cafés, bares, "
        "restaurantes, vida nocturna. La avenida Chapultepec es el eje. Muy caminable.\n"
        "• Zapopan: noroeste, moderno y pudiente. Andares (centro comercial alto), la Basílica "
        "de Zapopan, zona de tecnología.\n"
        "• Tlaquepaque: pueblo de artesanías y arte dentro del área metro, con El Parián "
        "(plaza de mariachis). Encantador, mejor jueves a domingo.\n"
        "• Tonalá: mercado y cerámica/artesanía, sobre todo jueves y domingos.\n"
        "Para quedarse: Chapultepec/Americana (ambiente y comida) o el Centro (historia)."
    ),
    "seguridad": (
        "SEGURIDAD (realista, sin alarmismo):\n"
        "• Las zonas turísticas del metro (Centro, Chapultepec, Zapopan, Tlaquepaque) son "
        "tranquilas con sentido común de día. El turista normalmente no es blanco.\n"
        "• De noche, muévete en Uber/DiDi, no caminando por calles solas o mal iluminadas.\n"
        "• Cajeros: úsalos DENTRO de bancos y de día; evita los de la calle de noche (el único "
        "riesgo real de 'secuestro exprés' es que te sigan tras sacar dinero en un cajero "
        "expuesto).\n"
        "• No manejes carreteras rurales de Jalisco de noche; no interactúes con quien ofrezca "
        "drogas; no fotografíes retenes policiales.\n"
        "• Emergencias: 911. Asistencia al turista (multilingüe): 078."
    ),
    "dinero": (
        "DINERO:\n"
        "• Moneda: peso mexicano (MXN). A junio 2026, ~17–19 MXN por dólar (varía).\n"
        "• Paga SIEMPRE en pesos: si el datáfono ofrece cobrarte en tu propia moneda (DCC), "
        "recházalo — aplica un tipo de cambio malo y cobra 5–10% de más.\n"
        "• Tarjeta se acepta en restaurantes, hoteles y supermercados. Lleva efectivo para "
        "taxis, mercados, comida callejera y propinas.\n"
        "• Cajeros: de bancos (Banamex, BBVA, Santander, Banorte, HSBC), dentro de sucursal o "
        "centro comercial.\n"
        "• Propina: 10–15% en restaurantes y NO suele venir incluida — déjala aparte. Taxis no "
        "esperan propina (se redondea)."
    ),
    "clima": (
        "CLIMA Y QUÉ LLEVAR:\n"
        "• Templado y seco casi todo el año: días de ~24–26°C, noches frescas de ~13–15°C.\n"
        "• Lleva ropa ligera para el día y algo abrigado para la noche.\n"
        "• Temporada de lluvias: junio a octubre, normalmente aguaceros fuertes por la tarde/"
        "noche que pasan rápido — un paraguas o impermeable ligero ayuda en esos meses.\n"
        "• El sol pega fuerte al mediodía: bloqueador e hidratación."
    ),
    "util": (
        "DATOS ÚTILES:\n"
        "• Agua: NO tomes agua del grifo. Usa agua embotellada o el garrafón del hotel; ojo "
        "con el hielo en puestos informales.\n"
        "• Altura (~1.560 m): suave, no suele dar mal de altura.\n"
        "• Enchufes: tipo A/B, 127V (igual que EE.UU.).\n"
        "• Emergencias: 911. Asistencia al turista (Ángeles Verdes, hablan inglés): 078.\n"
        "• Tequila: el pueblo está a ~60 km; hay tours y el José Cuervo Express. La birria y "
        "las tortas ahogadas son el platillo local emblemático."
    ),
}

CITY_GUIDES["guadalajara"] = _GUADALAJARA


def has_guide(city: str) -> bool:
    return norm_city(city) in CITY_GUIDES


def guide_for_city(city: str) -> dict | None:
    return CITY_GUIDES.get(norm_city(city))


def build_block(city: str) -> str:
    """Bloque de conocimiento de ciudad para inyectar en el system prompt.

    Devuelve "" si no hay guía curada para la ciudad — en ese caso el Companion
    debe ser honesto sobre lo que no sabe en vez de inventar, igual que con los
    lugares. Cadena lista para concatenar al prompt de context.build().
    """
    g = guide_for_city(city)
    if not g:
        return ""
    secciones = [g["resumen"], g["transporte"], g["zonas"],
                 g["seguridad"], g["dinero"], g["clima"], g["util"]]
    cuerpo = "\n\n".join(s for s in secciones if s)
    return (
        f"\nGUÍA LOCAL DE {g['display'].upper()} "
        "(conocimiento verificado de la ciudad — ÚSALO para responder sobre transporte, "
        "zonas, seguridad, dinero, clima y cómo moverse; habla con la autoridad de un "
        "local que vive aquí, no como un turista):\n"
        f"{cuerpo}\n"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Línea de emergencias por ciudad — para el system prompt. El Companion NO puede
# decirle "marca 123" a alguien en México; este dato cambia por país.
# Clave = ciudad normalizada (norm_city). Cada valor es una línea lista para el
# prompt con el número único y los datos de respaldo más útiles.
# ─────────────────────────────────────────────────────────────────────────────
EMERGENCY_LINES: dict[str, str] = {
    "bogota": (
        "EMERGENCIAS Colombia: 123 (único número, policía/ambulancia/bomberos). "
        "Farmacias 24h: Cruz Verde Calle 127 norte. Domicilios Cruz Verde: 486-5000."
    ),
    "cartagena": (
        "EMERGENCIAS Colombia: 123 (único número, policía/ambulancia/bomberos)."
    ),
    "guadalajara": (
        "EMERGENCIAS México: 911 (único número, policía/ambulancia/bomberos). "
        "Asistencia al turista (Ángeles Verdes, hablan inglés): 078. "
        "Respaldo de ambulancia (Cruz Roja): 065. Denuncia anónima: 089."
    ),
}


def emergency_line(city: str) -> str:
    """Línea de emergencias para el system prompt, según la ciudad/país.

    Si la ciudad no está mapeada, devuelve una guía neutra que NO inventa un
    número específico: el Companion debe orientar al número local sin afirmar
    uno equivocado.
    """
    linea = EMERGENCY_LINES.get(norm_city(city))
    if linea:
        return linea
    return (
        "EMERGENCIAS: usa el número de emergencias local del país donde está el "
        "usuario (no inventes uno). Si no lo conoces con certeza, dile que lo "
        "confirme con su hotel o recepción."
    )


# ─────────────────────────────────────────────────────────────────────────────
# País del DESTINO por ciudad — NO confundir con trip["pais"], que es la
# nacionalidad/origen del usuario (para emergencias y consulado). Esto es para
# armar bien las búsquedas del Destination Scanner (Tavily): "Guadalajara
# México noticias", no "Guadalajara Colombia".
# ─────────────────────────────────────────────────────────────────────────────
CITY_COUNTRY: dict[str, str] = {
    "bogota": "Colombia",
    "cartagena": "Colombia",
    "medellin": "Colombia",
    "cali": "Colombia",
    "guadalajara": "México",
    "ciudad de mexico": "México",
    "cancun": "México",
    "monterrey": "México",
}


def country_for_city(city: str) -> str:
    """País del DESTINO (no del usuario). Cadena vacía si no se conoce — en ese
    caso el scanner usa solo el nombre de la ciudad."""
    return CITY_COUNTRY.get(norm_city(city), "")
