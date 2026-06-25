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
        "• Quinta Camacho: barrio de casas inglesas antiguas en Chapinero, lleno de cafés "
        "de especialidad y restaurantes — bohemio, caminable y muy agradable de día.\n"
        "• Rosales: ladera tranquila y pudiente hacia los cerros, junto a la Zona G; "
        "residencial, buena para hospedarse cerca de la gastronomía.\n"
        "• La Macarena: barrio bohemio a los pies de los cerros, junto al centro — "
        "galerías de arte, restaurantes de autor y cafés. Para caminar, comer distinto y ver "
        "arte; ambiente artístico de día.\n"
        "• Parkway (La Soledad / Teusaquillo): alameda arborizada bohemia, con bares de "
        "vino, cafés y casas de los años 40 — plan tranquilo y caminable.\n"
        "• San Felipe: el distrito de galerías de arte (los 'Art Walk' de ciertos viernes); "
        "industrial reconvertido, alternativo, mejor con un plan en mano.\n"
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
    "tips": (
        "TIPS MUY LOCALES (para sonar, comer y moverte como un bogotano/rolo):\n"
        "COMIDA QUE TIENES QUE CONOCER (para entender cualquier carta o menú del día):\n"
        "• Ajiaco santafereño: LA sopa de Bogotá — tres papas (criolla, sabanera, pastusa), pollo, "
        "mazorca y guascas (una hierba local). Llega con crema, alcaparras y aguacate APARTE: se los "
        "echas tú al gusto, no viene mezclado.\n"
        "• Tamal santafereño: masa de maíz con pollo y cerdo envuelta en hoja de plátano; desayuno de "
        "domingo, se acompaña con chocolate caliente.\n"
        "• Changua: sopa de leche con huevo, cilantro y pan — desayuno típico de la sabana. Suena raro, "
        "es muy querido.\n"
        "• Caldo de costilla: el 'levantamuertos' — caldo de res con papa y cilantro, el desayuno "
        "anti-guayabo (anti-resaca) por excelencia.\n"
        "• Panes de queso que acompañan el café: almojábana, pandebono, pandeyuca y el buñuelo "
        "(bolita frita, va con natilla en diciembre).\n"
        "• Lechona tolimense: cerdo relleno de arroz y arveja, horneado — plato de fiesta. "
        "Fritanga/picada: tabla para compartir con carnes, chorizo, morcilla, papa criolla y chicharrón.\n"
        "• Chocolate 'santafereño' completo: chocolate caliente con un trozo de QUESO que se moja DENTRO "
        "del chocolate (no es error, es la costumbre) + almojábana.\n"
        "• Dulces: obleas (dos hostias con arequipe), postre de natas, cuajada con melao, brevas con "
        "arequipe.\n"
        "BEBIDAS (clave para no confundirte):\n"
        "• TINTO = café NEGRO pequeño, NO vino. Es la confusión #1 del extranjero. Te lo ofrecen en "
        "todas partes ('¿un tintico?'). Perico o pintado = café con leche pequeño.\n"
        "• Aguapanela = agua de panela caliente (a veces con limón o queso). Aguardiente ('guaro') = el "
        "trago nacional, sabe a anís. Refajo = cerveza + gaseosa Colombiana (roja, dulce). Canelazo = "
        "agua de panela con aguardiente, para el frío. Chicha = fermento de maíz, se prueba en el "
        "Chorro de Quevedo. Cervezas locales: Águila, Club Colombia, Poker.\n"
        "CÓMO HABLAR COMO ROLO:\n"
        "• '¿Me regala...?' = la forma cortés de pedir CUALQUIER cosa ('¿me regala un tinto?', '¿me "
        "regala la cuenta?'). No significa que sea gratis. 'A la orden' = lo que te dicen al entrar o "
        "salir de un local.\n"
        "• USTED: los bogotanos se tratan de 'usted' incluso entre amigos y parejas — no suena "
        "distante, es lo normal. 'Sumercé' es aún más cariñoso y respetuoso (de la sabana).\n"
        "• '¡Qué pena!' = perdón / disculpe / qué vergüenza (se usa muchísimo). '¡Qué pena con usted!' "
        "= una disculpa muy cortés.\n"
        "• Glosario rápido: parce/parcero = amigo; bacano/chévere = genial; berraco = bravo o "
        "impresionante; chino/pelado = muchacho; guayabo = resaca; camello = trabajo; lucas = miles de "
        "pesos ('diez lucas' = $10.000); rumbear = salir de fiesta; 'dar papaya' = exponerse o "
        "descuidarse; 'de una' = hagámoslo ya; 'listo' = ok.\n"
        "COSTUMBRES Y RITMOS DE LA CIUDAD:\n"
        "• Las ONCES: la merienda de media tarde (~4–6pm), café o chocolate con pan o algo dulce. Plan "
        "social muy bogotano — si te invitan a 'onces', es eso.\n"
        "• El CORRIENTAZO o menú del día: almuerzo completo (sopa + seco + jugo) económico, de ~12 a "
        "2pm en restaurantes de barrio. La mejor relación precio/comida del día; muy local.\n"
        "• Propina: la 'propina voluntaria' del 10% viene sugerida en la cuenta y te PREGUNTAN si la "
        "incluyes; es costumbre aceptarla si te atendieron bien. Con datáfono a la mesa te preguntan "
        "'¿cuántas cuotas?' — di 'una'.\n"
        "• Festivos y PUENTES: Colombia tiene muchos lunes festivos. La ciudad se vacía (buen tráfico "
        "interno) pero algunos museos y restaurantes cambian horario o cierran, y las vías de salida se "
        "congestionan el viernes y el lunes del puente.\n"
        "• Domingo: Ciclovía (7am–2pm) y día de plan familiar; abundan los almuerzos pero varios bares "
        "y restaurantes de noche cierran. 'Ley seca': en días de elecciones se prohíbe vender alcohol — "
        "si cae en tu viaje, abastécete antes.\n"
        "• Pico y placa: restricción vehicular por número de placa en horas pico — sube la tarifa "
        "dinámica y la espera de Uber/taxi a esas horas; cuenta con unos minutos más al pedir carro."
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
        "Guadalajara (GDL), capital de Jalisco en el occidente de México, a ~1.560 m de altura "
        "— clima templado y seco casi todo el año, no es altura que marée. Cuna del tequila, el "
        "mariachi, la birria y la torta ahogada. A la gente y a lo de aquí se le dice 'tapatío/a'. "
        "La ciudad es plana y ordenada en cuadrícula, fácil de orientar. La zona metropolitana une "
        "varios municipios: Guadalajara (Centro Histórico, Colonia Americana, Providencia), Zapopan "
        "(moderno y pudiente: Andares, Puerta de Hierro, la Basílica), Tlaquepaque y Tonalá "
        "(artesanías). El pueblo de Tequila queda a ~60 km al oeste y el Lago de Chapala a ~45 min al sur."
    ),
    "transporte": (
        "MOVERSE EN GUADALAJARA (la ciudad es plana y se mueve bien; con esto te mueves sin problema):\n"
        "• APPS DE CARRO — lo más práctico, seguro y barato. Uber, DiDi e inDriver funcionan "
        "excelente y casi siempre son más baratas que un taxi de calle. Pídelas SIEMPRE por app "
        "y verifica placa y modelo antes de subir; nunca pares un taxi en la calle. Rangos reales "
        "(varían por hora y demanda): trayecto corto dentro de la Americana/Centro/Providencia "
        "~$50–110 MXN; cruzar la ciudad (p.ej. Centro→Zapopan/Andares) ~$120–220 MXN; del "
        "aeropuerto al Centro/Americana ~$250–380 MXN.\n"
        "• TAXIS: si no hay app, que el hotel llame a un 'sitio' (taxi regulado). Muchos no usan "
        "taxímetro — acuerda el precio ANTES de subir.\n"
        "• MI TREN (tren ligero, SITEUR) — limpio, rápido y baratísimo (~$9.50 MXN por viaje con "
        "tarjeta Mi Movilidad, recargable en estaciones). 3 líneas. La que más sirve al viajero es "
        "la LÍNEA 3: conecta Zapopan centro ↔ CENTRO HISTÓRICO (estaciones Catedral / Guadalajara "
        "Centro, junto a la Plaza de Armas) ↔ San Pedro Tlaquepaque. Es decir, une de un tirón tres "
        "de las zonas que más vas a visitar, evitando el tráfico. L1 corre norte–sur y L2 oriente–poniente.\n"
        "• MACROBÚS / MI MACRO: buses troncales por carril exclusivo (el de Calzada Independencia "
        "cruza el Centro de norte a sur). Económico; misma lógica de tarjeta.\n"
        "• MIBICI: bicis públicas con estaciones en Centro, Colonia Americana, Lafayette, Chapultepec "
        "y Providencia — ideales para esas zonas, que son planas. Hay pase de 1, 3 o 7 días (desde "
        "~$108 MXN) que se saca en la app MiBici.\n"
        "• DESDE EL AEROPUERTO (GDL, Miguel Hidalgo y Costilla, a ~17 km al sur): ~20–30 min al "
        "Centro en auto. Opciones: 1) Uber/DiDi — tienen zona de abordaje señalizada, revisa en la "
        "app del día el punto exacto de pickup. 2) Taxi oficial del aeropuerto (mostradores con "
        "tarifa por zona prepagada, ~$350–550 MXN según destino) — más caro pero fijo. El tren "
        "ligero NO llega al aeropuerto.\n"
        "• A PIE: Centro Histórico, Colonia Americana, Lafayette, Chapultepec y Providencia son "
        "muy caminables y agradables de día. Para ir de una zona a otra, mejor app o Mi Tren.\n"
        "• MANEJAR: el tráfico es notable en hora pico (8–10am, 6–8pm) y estacionarse en Centro y "
        "Americana es complicado; para el viajero casi siempre conviene más app que rentar carro, "
        "salvo para excursiones (Tequila, Chapala)."
    ),
    "zonas": (
        "ZONAS CLAVE (qué es cada una y para qué sirve):\n"
        "• CENTRO HISTÓRICO: el corazón monumental — Catedral, Plaza de Armas, Teatro Degollado, "
        "Plaza Tapatía, Hospicio Cabañas (murales de Orozco) y el Mercado San Juan de Dios. "
        "Imperdible DE DÍA; caminable. Se anima de día, de noche pierde gente.\n"
        "• COLONIA AMERICANA (con Lafayette y la Av. Chapultepec): el barrio cool de la ciudad, "
        "premiado entre los más 'cool' del mundo. Cafés de especialidad, bares, mezcalerías, "
        "restaurantes de autor, terrazas y vida nocturna. Muy caminable; el público sale desde "
        "las 21:00. La mejor base para alojarse si quieres ambiente y comida.\n"
        "• PROVIDENCIA: arbolada, acomodada y tranquila al poniente, con una de las mejores "
        "concentraciones de restaurantes y cafés (mariscos, japonés de autor). Alternativa más "
        "calmada y refinada a la Americana.\n"
        "• CHAPALITA: colonia residencial agradable, de glorieta con tianguis dominical, buena para "
        "comer rico sin bullicio.\n"
        "• SANTA TERESITA ('Santa Tere'): barrio tradicional con mercado y fondas auténticas; "
        "muy local.\n"
        "• ZAPOPAN: municipio moderno y pudiente al noroeste. Su Centro tiene la Basílica de "
        "Zapopan y andador; la zona de ANDARES / PUERTA DE HIERRO es de centros comerciales de "
        "lujo, torres y hospitales privados.\n"
        "• TLAQUEPAQUE (San Pedro Tlaquepaque): pueblo de artesanías y arte dentro del área metro "
        "(~20 min), con calles empedradas, galerías, El Parián (mariachi en vivo) y restaurantes "
        "con terraza. Encantador; lindo también de noche por la iluminación.\n"
        "• TONALÁ: cuna de la cerámica y el barro; su tianguis (jueves y domingo) es para comprar "
        "artesanía directo del productor.\n"
        "Para quedarse: Colonia Americana/Lafayette (ambiente y comida), Providencia (tranquilo y "
        "rico) o el Centro (historia y precio)."
    ),
    "seguridad": (
        "SEGURIDAD (realista, sin alarmismo — Guadalajara es muy disfrutable con sentido común):\n"
        "ZONAS SEGURAS / TURÍSTICAS (tranquilas con cuidado normal, de día y buena parte de la "
        "noche): Colonia Americana, Lafayette, Av. Chapultepec, Providencia, Chapalita, Centro "
        "Histórico (de día), Zapopan centro, Andares/Puerta de Hierro y el centro de Tlaquepaque. "
        "El turista en la zona metropolitana normalmente NO es blanco de nada.\n"
        "ZONAS / MOMENTOS DONDE TENER MÁS CUIDADO:\n"
        "• El Centro Histórico DE NOCHE pierde gente: muévete en app, no caminando por calles "
        "solas, y guarda el celular.\n"
        "• El entorno del Mercado San Juan de Dios y la Plaza de los Mariachis: visítalos de día o "
        "temprano en la noche; de noche cerrada la zona se pone solitaria y hay carterismo. Cuida "
        "tus cosas en el mercado (mucha gente).\n"
        "• El tianguis El Baratillo (domingos, oriente) y los tianguis muy concurridos: increíbles, "
        "pero ve ligero, sin joyas y con el dinero al frente — carterismo de oportunidad.\n"
        "• Colonias del oriente y sur lejano (fuera del circuito turístico, p.ej. Oblatos, San "
        "Andrés y periferias) no son para pasear sin motivo, sobre todo de noche. No es que sean "
        "'prohibidas', simplemente no hay nada turístico ahí y no vale exponerse.\n"
        "REGLAS DE ORO:\n"
        "• De noche, app en vez de caminar tramos largos o por calles mal iluminadas.\n"
        "• Cajeros: úsalos DENTRO de bancos, OXXO o centros comerciales y de día. El riesgo real de "
        "'secuestro exprés' es que te sigan tras sacar dinero en un cajero expuesto de calle.\n"
        "• No manejes carreteras rurales de Jalisco de noche. No interactúes con quien ofrezca o "
        "pida droga. No fotografíes retenes, militares ni convoyes. Si ves un bloqueo o ambiente "
        "raro, da media vuelta sin grabar.\n"
        "• No dejes tu trago solo en bares; cuida bebidas.\n"
        "• Emergencias: 911. Asistencia al turista (multilingüe / Ángeles Verdes): 078."
    ),
    "dinero": (
        "DINERO:\n"
        "• Moneda: peso mexicano (MXN). A junio 2026, ~17–19 MXN por dólar (varía).\n"
        "• Paga SIEMPRE en pesos: si el datáfono ofrece cobrarte en tu propia moneda (DCC), "
        "recházalo — aplica un tipo de cambio malo y cobra 5–10% de más.\n"
        "• Tarjeta se acepta en restaurantes, hoteles y supermercados. Lleva efectivo para "
        "taxis de sitio, tianguis, comida callejera, mercados y propinas (muchas fondas y puestos "
        "son solo efectivo).\n"
        "• Cajeros: de bancos (Banamex, BBVA, Santander, Banorte, HSBC), dentro de sucursal o "
        "centro comercial. Avísale a tu banco que viajas.\n"
        "• Propina: 10–15% en restaurantes y NO suele venir incluida — déjala aparte y no la "
        "asumas en la cuenta. A los del mariachi se les paga por canción (acuerda antes el precio). "
        "Taxis no esperan propina (se redondea)."
    ),
    "clima": (
        "CLIMA Y QUÉ LLEVAR:\n"
        "• Templado y seco casi todo el año: días de ~24–28°C, noches frescas de ~13–16°C — el "
        "clásico cambio de chamarra al caer el sol.\n"
        "• Lleva ropa ligera para el día y algo abrigado para la noche.\n"
        "• Temporada de lluvias: junio a octubre. Suelen ser aguaceros fuertes por la tarde/noche "
        "que pasan rápido — lleva paraguas o impermeable ligero y planea lo de exterior para la "
        "mañana en esos meses.\n"
        "• El sol pega fuerte al mediodía aunque haga fresco: bloqueador e hidratación."
    ),
    "util": (
        "DATOS ÚTILES:\n"
        "• Agua: NO tomes agua del grifo. Usa agua embotellada o el garrafón del hotel; ojo con el "
        "hielo en puestos muy informales (los establecidos usan hielo purificado).\n"
        "• Altura (~1.560 m): suave, no suele dar mal de altura.\n"
        "• Enchufes: tipo A/B, 127V (igual que EE.UU.).\n"
        "• Conveniencia: hay OXXO y 7-Eleven en cada esquina para agua, snacks, recargas y hasta "
        "pagar servicios; supermercados (Soriana, Walmart, La Comer, Chedraui) en plazas.\n"
        "• Farmacias: la cadena Farmacias Guadalajara (local, nació aquí) tiene muchas sucursales "
        "'Super Farmacia' abiertas 24h con consultorio médico barato al lado — clave para un "
        "malestar menor.\n"
        "• Emergencias: 911. Asistencia al turista (Ángeles Verdes, hablan inglés): 078. Cruz Roja: 065."
    ),
    "tips": (
        "TIPS MUY LOCALES (para sonar y moverte como tapatío):\n"
        "• COMIDA QUE NO TE PUEDES PERDER: torta ahogada (birote salado relleno de carnitas, "
        "'ahogado' en salsa de jitomate y MUCHO chile de árbol — pídela 'media ahogada' si no "
        "aguantas el picante, 'bien ahogada' si sí); birria tatemada (tradicional de chivo, con su "
        "consomé aparte — pide 'maciza' si la quieres sin tanto hueso); carne en su jugo (invento "
        "tapatío); y de postre, JERICALLA (un flan/natilla quemado por arriba, dulce típico de "
        "aquí, no te vayas sin probarlo).\n"
        "• BEBIDAS LOCALES: tejuino (bebida fría fermentada de maíz con limón, sal y chile, se "
        "vende en carrito — muy local); cantaritos (tequila con cítricos y refresco de toronja en "
        "jarro de barro, clásico de cantina); y por supuesto tequila derecho con sangrita, no de "
        "shot turístico.\n"
        "• HORARIOS: aquí se cena y se sale TARDE. Los bares de la Americana se llenan después de "
        "las 22:00; ir a las 20:00 es llegar antes que nadie. Los antros, después de medianoche.\n"
        "• TIANGUIS Y ARTESANÍA: Tlaquepaque y Tonalá lucen más jueves y domingo (días de "
        "tianguis). El Baratillo (domingo) es el tianguis enorme para curiosear de todo.\n"
        "• MARIACHI: nació aquí. Para vivirlo, Plaza de los Mariachis (de día/temprano) o El Parián "
        "de Tlaquepaque. Una canción se paga aparte: pregunta el precio ANTES de pedirla.\n"
        "• FÚTBOL: la ciudad respira fútbol. Chivas (Estadio Akron, en Zapopan) y Atlas son los "
        "equipos; si hay partido o clásico tapatío, evita ponerte colores del equipo rival 'por si "
        "acaso' y cuenta con más tráfico.\n"
        "• EXPRESIONES: 'tapatío/a' = de Guadalajara; aquí dicen '¿mande?' en vez de '¿qué?', y "
        "'¿qué onda?' para saludar. 'Birote' es el pan local (clave en la torta ahogada).\n"
        "• Pedir un Uber/DiDi y esperar adentro de un local o lobby (no en la banqueta sola de "
        "noche) es lo que hace un local."
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
                 g["seguridad"], g["dinero"], g["clima"], g["util"],
                 g.get("tips", "")]
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


# Idioma que se HABLA en el destino (no el del usuario). Es el idioma DESTINO del
# traductor: las frases que el viajero necesita decirle a un local. Para destinos
# del mismo idioma del usuario, el traductor se convierte en glosario local.
CITY_LANG: dict[str, str] = {
    "bogota": "es",
    "cartagena": "es",
    "medellin": "es",
    "cali": "es",
    "guadalajara": "es",
    "ciudad de mexico": "es",
    "cancun": "es",
    "monterrey": "es",
}

# Nombre legible del idioma para inyectar en el prompt del Companion.
LANG_NAME: dict[str, str] = {
    "es": "español latinoamericano",
    "en": "English",
    "pt": "português (Brasil)",
    "fr": "français",
}


def lang_for_city(city: str) -> str:
    """Idioma que se habla en el destino (código ISO corto). 'es' por defecto."""
    return CITY_LANG.get(norm_city(city), "es")


def lang_name(code: str) -> str:
    """Nombre legible del idioma del usuario, para el system prompt."""
    return LANG_NAME.get((code or "es").lower(), LANG_NAME["es"])
