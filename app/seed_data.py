"""Seed de destination_places — curación inicial, una ciudad por lista.

Cada lugar: zona (referencia descriptiva — barrio/área del lugar), categoría,
coordenadas PROPIAS del lugar, descripción ya redactada para el motor, y
nivel de confianza (consenso entre fuentes + tier de la fuente).

FUENTES de esta curación (jun. 2026): Latin America's 50 Best Restaurants
2025 (El Espectador, El Tiempo), The World's 100 Best Coffee Shops 2026
(Infobae/Portafolio), ranking Cielo.Travel (may. 2026), Revista Diners
(feb. 2026), guías locales (Lure, Exclama, DistritoCH) y señales sociales
reportadas (TikTok discover). Cada descripción menciona su fuente para que
el motor de scoring pueda comunicar POR QUÉ se recomienda.

COORDENADAS: aproximaciones a nivel calle/carrera derivadas de la dirección
conocida de cada lugar (la cuadrícula de Bogotá es regular: calles suben al
norte, carreras al occidente) — NO son geocoding exacto (eso es Nivel 2,
requiere Google Maps Geocoding API). Suficientes para rankear por distancia;
el maps_link usa el NOMBRE del lugar, así que "Cómo llegar" siempre resuelve
a la dirección real aunque la coordenada tenga unos metros de error.

Categorías (texto libre, el scoring las lee en el payload): restaurante,
cafe, bar, parque, atraccion, mercado.

Esto se llama una sola vez (idempotente) desde db.seed_destination_places()
al iniciar la app. Ampliar la lista = agregar diccionarios aquí.
"""
from . import geo

BOGOTA = [
    # ════════ RESTAURANTES — alta cocina (50 Best y rankings) ════════
    {
        "city_display": "Bogotá", "name": "El Chato", "category": "restaurante", "zona": "Zona G",
        "lat": 4.6605, "lng": -74.0571,
        "descripcion": "Del chef Álvaro Clavijo. Llegó al puesto #2 de Latin America's 50 Best Restaurants y #6 en el ranking de Cielo.Travel (2026). Alta cocina colombiana de temporada, cocina abierta. Reservar con anticipación.",
        "confianza": "muy_alta",
    },
    {
        "city_display": "Bogotá", "name": "Leo Cocina y Cava", "category": "restaurante", "zona": "La Macarena / Centro Internacional",
        "lat": 4.6113, "lng": -74.0689,
        "descripcion": "De la chef Leonor Espinosa, #10 en Latin America's 50 Best 2025. Referencia internacional de alta cocina colombiana con ingredientes nativos del territorio. Menú degustación.",
        "confianza": "muy_alta",
    },
    {
        "city_display": "Bogotá", "name": "Humo Negro", "category": "restaurante", "zona": "Chapinero Alto",
        "lat": 4.6445, "lng": -74.0622,
        "descripcion": "Del chef Jaime Torregrosa (ex jefe de cocina de El Chato). #41 en Latin America's 50 Best 2025. Izakaya japonesa fusionada con sabores latinoamericanos y nórdicos, producto colombiano sostenible (pirarucú amazónico). Carrera 5 #56-06.",
        "confianza": "muy_alta",
    },
    {
        "city_display": "Bogotá", "name": "Mesa Franca", "category": "restaurante", "zona": "Chapinero Alto",
        "lat": 4.6480, "lng": -74.0612,
        "descripcion": "Del chef Iván Cadena, presente en los Latin America's 50 Best (lista extendida). Cocina de autor de sabores criollos con técnicas nativas; coctelería con viche del Pacífico (Mula Pacífica). Calle 61 #5-56.",
        "confianza": "alta",
    },
    {
        "city_display": "Bogotá", "name": "Salvo Patria", "category": "restaurante", "zona": "Chapinero Alto",
        "lat": 4.6462, "lng": -74.0592,
        "descripcion": "Presente en los Latin America's 50 Best (lista extendida). 15 años de cocina colombiana de temporada en casa de Chapinero Alto; platos para compartir como los agnolotti de mazorca con mantequilla de hormiga. Cra 4 Bis #58-60.",
        "confianza": "alta",
    },
    {
        "city_display": "Bogotá", "name": "Harry Sasson", "category": "restaurante", "zona": "Zona G",
        "lat": 4.6612, "lng": -74.0605,
        "descripcion": "Clásico de la gastronomía bogotana, presente en los Latin America's 50 Best (lista extendida). Su chef recibió el premio Icon 2024 de los 50 Best por 30 años de carrera. Técnica clásica, gran producto, servicio impecable.",
        "confianza": "alta",
    },
    # ════════ RESTAURANTES — tradicionales e icónicos ════════
    {
        "city_display": "Bogotá", "name": "La Puerta Falsa", "category": "restaurante", "zona": "La Candelaria",
        "lat": 4.5978, "lng": -74.0757,
        "descripcion": "Icónico, centenario, junto a la Plaza de Bolívar. Famoso por tamales y chocolate santafereño. #15 en el ranking de Cielo.Travel.",
        "confianza": "alta",
    },
    {
        "city_display": "Bogotá", "name": "Restaurante Santa Fe", "category": "restaurante", "zona": "La Candelaria",
        "lat": 4.5964, "lng": -74.0748,
        "descripcion": "Cocina tradicional bogotana, ajiaco santafereño premiado. #14 en el ranking de Cielo.Travel.",
        "confianza": "media_alta",
    },
    {
        "city_display": "Bogotá", "name": "Pizzardi Artigianale", "category": "restaurante", "zona": "Zona G",
        "lat": 4.6594, "lng": -74.0586,
        "descripcion": "Certificación oficial True Neapolitan Pizza Association, considerada la mejor pizza de Colombia. #13 en el ranking de Cielo.Travel.",
        "confianza": "alta",
    },
    {
        "city_display": "Bogotá", "name": "La Cabrera", "category": "restaurante", "zona": "Zona G",
        "lat": 4.6573, "lng": -74.0559,
        "descripcion": "Reconocido por sus cortes de carne (steaks). Mencionado en guías de restaurantes de Zona G.",
        "confianza": "media",
    },
    {
        "city_display": "Bogotá", "name": "La Lucha Sanguchería", "category": "restaurante", "zona": "Zona G",
        "lat": 4.6561, "lng": -74.0554,
        "descripcion": "Sandwichería peruana con varias sedes, opción informal y rápida en Zona G.",
        "confianza": "media",
    },
    {
        "city_display": "Bogotá", "name": "Abasto", "category": "restaurante", "zona": "Usaquén",
        "lat": 4.6958, "lng": -74.0295,
        "descripcion": "Bistró de mercado con más de 15 años, ingrediente local y de temporada, panadería propia. Recomendado por Revista Diners (feb. 2026); muy concurrido el brunch dominical. Calle 118 #5-41.",
        "confianza": "alta",
    },
    {
        "city_display": "Bogotá", "name": "Abasto Quinta Camacho", "category": "restaurante", "zona": "Quinta Camacho",
        "lat": 4.6532, "lng": -74.0601,
        "descripcion": "Segunda sede del bistró Abasto, en casa de Quinta Camacho — misma cocina de inspiración nacional con producto local, admite mascotas en el almuerzo. Calle 69A #9-09.",
        "confianza": "media_alta",
    },
    {
        "city_display": "Bogotá", "name": "El Don (Chapinero)", "category": "restaurante", "zona": "Chapinero",
        "lat": 4.6462, "lng": -74.0632,
        "descripcion": "Restaurante con terrazas muy fotogénicas y menú amplio a buen precio — recurrente en recomendaciones de creadores en TikTok (2026) como lugar 'que sí o sí hay que conocer'. Calle 59 #6-36.",
        "confianza": "media",
    },
    # ════════ CAFÉ DE ESPECIALIDAD Y PANADERÍAS ════════
    {
        "city_display": "Bogotá", "name": "Tropicalia Coffee", "category": "cafe", "zona": "Chapinero Alto",
        "lat": 4.6660, "lng": -74.0558,
        "descripcion": "Puesto #9 mundial en The World's 100 Best Coffee Shops 2026 — la cafetería de Colombia mejor posicionada. Métodos V60, Chemex, Aeropress, espresso; brunch y catas guiadas. Calle 81a #8-23.",
        "confianza": "muy_alta",
    },
    {
        "city_display": "Bogotá", "name": "Amor Perfecto", "category": "cafe", "zona": "Usaquén",
        "lat": 4.6940, "lng": -74.0325,
        "descripcion": "Cadena colombiana pionera del café de especialidad, reconocida por acercar cafés premium de origen único al consumidor local. Buena parada de café cerca del mercado de Usaquén.",
        "confianza": "alta",
    },
    {
        "city_display": "Bogotá", "name": "Colo Coffee", "category": "cafe", "zona": "Parque de la 93 (Chicó)",
        "lat": 4.6757, "lng": -74.0478,
        "descripcion": "Cafetería de especialidad amplia, con WiFi rápido — citada en rutas de café de especialidad de Bogotá (2026) como buen lugar para trabajar o pasar la tarde cerca de la Calle 93.",
        "confianza": "media_alta",
    },
    {
        "city_display": "Bogotá", "name": "Brot Bakery & Café", "category": "cafe", "zona": "Zona T / El Nogal",
        "lat": 4.6648, "lng": -74.0555,
        "descripcion": "Panadería-café clásica de Bogotá desde 1999, famosa por su baguette de chocolate y sus desayunos. Recurrente en los tops de brunch de la ciudad (Lure, Tripadvisor). Calle 81 #7-93, con terraza.",
        "confianza": "alta",
    },
    {
        "city_display": "Bogotá", "name": "Masa (Rosales)", "category": "cafe", "zona": "Zona G / Rosales",
        "lat": 4.6541, "lng": -74.0563,
        "descripcion": "Panadería y brunch de referencia: croissant de almendras, baguette recién horneado, pan de canela. Citada en los mejores brunch de Bogotá (Revista Exclama). Calle 70 #4-83.",
        "confianza": "alta",
    },
    {
        "city_display": "Bogotá", "name": "Árbol del Pan", "category": "cafe", "zona": "Chapinero Alto",
        "lat": 4.6512, "lng": -74.0570,
        "descripcion": "Panadería artesanal encantadora, destino de brunch muy querido en Chapinero — entre las panaderías mejor valoradas de la ciudad. Cra 4 #66-46.",
        "confianza": "media_alta",
    },
    # ════════ BARES Y VIDA NOCTURNA ════════
    {
        "city_display": "Bogotá", "name": "KOSH", "category": "bar", "zona": "Zona T",
        "lat": 4.6685, "lng": -74.0535,
        "descripcion": "Rooftop bar frente al Centro Comercial Andino con vista panorámica a la Zona T. Cócteles de autor y ambiente nocturno — punto de referencia para salir de fiesta en la zona.",
        "confianza": "alta",
    },
    {
        "city_display": "Bogotá", "name": "Clandestino (Gallery Club)", "category": "bar", "zona": "Zona T",
        "lat": 4.6678, "lng": -74.0542,
        "descripcion": "Se presenta como el primer Gallery Club de Suramérica: club nocturno + galería de arte en plena Zona T. Citado entre los mejores bares de Bogotá (Reservándonos, 2025).",
        "confianza": "media",
    },
    {
        "city_display": "Bogotá", "name": "Taninos Park Wines", "category": "bar", "zona": "Parkway / La Soledad",
        "lat": 4.6285, "lng": -74.0725,
        "descripcion": "Bar de vinos en el Parkway con más de 140 etiquetas de distintas regiones del mundo — plan tranquilo de copas, citado entre los mejores bares de Bogotá. Zona bohemia y caminable.",
        "confianza": "media",
    },
    {
        "city_display": "Bogotá", "name": "Zona T / Zona Rosa (vida nocturna)", "category": "atraccion", "zona": "Zona T",
        "lat": 4.6678, "lng": -74.0528,
        "descripcion": "Calles peatonales con la mayor oferta de bares y discotecas de la ciudad, entre las carreras 11-15 y calles 82-86.",
        "confianza": "alta",
    },
    # ════════ MUSEOS Y CULTURA ════════
    {
        "city_display": "Bogotá", "name": "Museo del Oro", "category": "atraccion", "zona": "La Candelaria",
        "lat": 4.6019, "lng": -74.0721,
        "descripcion": "Una de las colecciones de oro prehispánico más importantes del mundo. Imprescindible en el centro histórico.",
        "confianza": "muy_alta",
    },
    {
        "city_display": "Bogotá", "name": "Museo Botero", "category": "atraccion", "zona": "La Candelaria",
        "lat": 4.5973, "lng": -74.0726,
        "descripcion": "Colección donada por Fernando Botero en casa colonial restaurada: obras suyas más Picasso, Dalí, Renoir y Monet. Entrada GRATUITA. A 10 min a pie del Museo del Oro. Calle 11 #4-41.",
        "confianza": "muy_alta",
    },
    {
        "city_display": "Bogotá", "name": "Plaza de Bolívar", "category": "atraccion", "zona": "La Candelaria",
        "lat": 4.5981, "lng": -74.0760,
        "descripcion": "Corazón político e histórico de Bogotá: Catedral Primada, Capitolio Nacional y Palacio de Justicia alrededor. Punto de partida natural del centro histórico.",
        "confianza": "muy_alta",
    },
    {
        "city_display": "Bogotá", "name": "Chorro de Quevedo", "category": "atraccion", "zona": "La Candelaria",
        "lat": 4.5970, "lng": -74.0699,
        "descripcion": "Plazoleta donde la tradición ubica la fundación de Bogotá. Ambiente bohemio, cuenteros, chicha y arte urbano — parada clásica del recorrido por La Candelaria.",
        "confianza": "alta",
    },
    {
        "city_display": "Bogotá", "name": "Caminata por La Candelaria (grafiti y arquitectura colonial)", "category": "atraccion", "zona": "La Candelaria",
        "lat": 4.5967, "lng": -74.0739,
        "descripcion": "Tour a pie por el barrio histórico, arte urbano y arquitectura colonial. Punto clave para visitantes primerizos.",
        "confianza": "alta",
    },
    # ════════ MIRADORES Y NATURALEZA ════════
    {
        "city_display": "Bogotá", "name": "Cerro de Monserrate", "category": "atraccion", "zona": "La Candelaria / Cerros",
        "lat": 4.6058, "lng": -74.0557,
        "descripcion": "Teleférico/funicular con vista panorámica de toda la ciudad. Mencionado en todas las guías como punto de partida del día 1.",
        "confianza": "muy_alta",
    },
    {
        "city_display": "Bogotá", "name": "Mirador Torre Colpatria", "category": "atraccion", "zona": "Centro Internacional",
        "lat": 4.6125, "lng": -74.0703,
        "descripcion": "Vista panorámica de 360° de Bogotá desde 196m, en el primer rascacielos de la ciudad (1979). Abierto solo viernes, sábados, domingos y festivos — confirmar horario antes de ir. Cra 7 #24-89.",
        "confianza": "muy_alta",
    },
    {
        "city_display": "Bogotá", "name": "Jardín Botánico José Celestino Mutis", "category": "parque", "zona": "Salitre",
        "lat": 4.6685, "lng": -74.0995,
        "descripcion": "El jardín botánico de Bogotá: lago, colección de plantas exóticas, palmetum y réplica de bosque de robles. Plan tranquilo de medio día, ideal con buen clima.",
        "confianza": "alta",
    },
    {
        "city_display": "Bogotá", "name": "Parque Metropolitano Simón Bolívar", "category": "parque", "zona": "Salitre",
        "lat": 4.6580, "lng": -74.0930,
        "descripcion": "El gran pulmón verde de Bogotá — lago, ciclorrutas y eventos masivos. Para caminar, hacer picnic o ejercicio al aire libre.",
        "confianza": "alta",
    },
    {
        "city_display": "Bogotá", "name": "Parque de la 93", "category": "parque", "zona": "Parque de la 93 (Chicó)",
        "lat": 4.6766, "lng": -74.0487,
        "descripcion": "Parque urbano rodeado de restaurantes, cafés y terrazas. Ideal para un plan tranquilo al aire libre o tarde de fotos; suele tener actividades los fines de semana.",
        "confianza": "muy_alta",
    },
    # ════════ MERCADOS Y EXPERIENCIAS LOCALES ════════
    {
        "city_display": "Bogotá", "name": "Mercado de pulgas de Usaquén", "category": "mercado", "zona": "Usaquén",
        "lat": 4.6952, "lng": -74.0312,
        "descripcion": "Mercado de antigüedades y artesanías, especialmente recomendado los domingos. Barrio tradicional y caminable.",
        "confianza": "alta",
    },
    {
        "city_display": "Bogotá", "name": "Plaza de Mercado de Paloquemao", "category": "mercado", "zona": "Paloquemao",
        "lat": 4.6155, "lng": -74.0850,
        "descripcion": "La plaza de mercado más famosa de Bogotá: frutas exóticas colombianas, flores, hierbas y desayuno local entre puestos. Mejor temprano en la mañana. Experiencia gastronómica imperdible.",
        "confianza": "alta",
    },
    # ════════ EXCURSIONES (fuera de la ciudad) ════════
    {
        "city_display": "Bogotá", "name": "Catedral de Sal de Zipaquirá", "category": "atraccion", "zona": "Zipaquirá",
        "lat": 5.0211, "lng": -73.9909,
        "descripcion": "Catedral subterránea excavada en una mina de sal. Día completo, fuera de la ciudad — no es un plan 'cerca', es una excursión.",
        "confianza": "alta",
    },
]

# Fallback para futuras entradas sin lat/lng propios y cuya zona tampoco esté
# en geo.ZONE_COORDS (caso de Zipaquirá, que no es una zona de la ciudad).
_ZIPAQUIRA = (5.0211, -73.9909)


def all_seeds() -> list[dict]:
    """Resuelve lat/lng de cada lugar.

    Prioridad: 1) lat/lng propios del lugar (todas las entradas actuales).
    2) si una entrada futura no trae coordenadas propias, fallback al punto
    de referencia de su zona (geo.zone_coords) o a _ZIPAQUIRA.
    """
    out = []
    for p in BOGOTA:
        if "lat" in p and "lng" in p:
            lat, lng = p["lat"], p["lng"]
        elif p["zona"] == "Zipaquirá":
            lat, lng = _ZIPAQUIRA
        else:
            coords = geo.zone_coords(p["city_display"], p["zona"])
            lat, lng = coords if coords else (0.0, 0.0)
        out.append({**p, "lat": lat, "lng": lng})
    return out
