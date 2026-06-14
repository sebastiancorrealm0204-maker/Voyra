"""Seed de destination_places — curación de Bogotá del Companion.

Cada lugar: zona (barrio/área — también es la etiqueta humana que devuelve el
reverse-geocode), categoría, coordenadas PROPIAS del lugar, una descripción ya
redactada para el motor (dice POR QUÉ se recomienda y, cuando aplica, su fuente),
nivel de confianza y, para landmarks/lugares de nombre único, `maps_query`
(nombre buscable → Google Maps lleva al pin oficial, "Cómo llegar" más preciso
que una coordenada aproximada).

Campo `dir`: dirección real del lugar. NO se guarda en la BD (db lo ignora);
sirve para (1) que el Companion pueda decir la dirección y (2) regenerar
`geocode_seed.py` y verificar coordenadas con Nominatim. Las coordenadas aquí
son aproximaciones a nivel calle/carrera (la cuadrícula de Bogotá es regular:
calles suben al norte, carreras al occidente) — suficientes para rankear por
distancia. Para precisión fina, correr `geocode_seed.py` una vez.

FUENTES (jun. 2026): Latin America's 50 Best Restaurants 2025 (ceremonia 2 dic
2025, Antigua) y su lista extendida 51–100; The World's 100 Best Coffee Shops
2026; Guía Michelin Colombia 2025 (NOTA: en Colombia Michelin solo otorga
"Llaves" a HOTELES — NO hay estrellas de restaurantes en el país, no se
afirman); Revista Diners, Revista Exclama, guías locales (Lure, DistritoCH) y
señales sociales reportadas. Lo "viral de la semana" NO va aquí — eso lo
aporta el Destination Scanner en vivo; este seed es lo atemporal y verificable.

Categorías (texto libre que lee el scoring): restaurante, cafe, bar, parque,
atraccion, mercado, mirador, compras, experiencia, excursion.

Idempotente: db.seed_destination_places() inserta una sola vez. Ampliar = agregar
diccionarios aquí (y re-sembrar: rm voyra.db).
"""
from . import geo

BOGOTA = [
    # ════════════ ALTA COCINA — rankings internacionales ════════════
    {
        "city_display": "Bogotá", "name": "El Chato", "category": "restaurante", "zona": "Chapinero Alto",
        "dir": "Calle 65 #4-76, Chapinero, Bogotá, Colombia",
        "maps_query": "El Chato restaurante Bogotá",
        "lat": 4.6402, "lng": -74.0625,
        "descripcion": "El #1 de Latin America's 50 Best Restaurants 2025 — nombrado el Mejor Restaurante de Latinoamérica. Del chef Álvaro Clavijo: alta cocina colombiana de temporada que realza el producto local con técnica global. Reservar con semanas de anticipación.",
        "confianza": "muy_alta",
    },
    {
        "city_display": "Bogotá", "name": "Leo Cocina y Cava", "category": "restaurante", "zona": "Chapinero / Centro Internacional",
        "dir": "Calle 27B #6-75, Bogotá, Colombia",
        "maps_query": "Leo Cocina y Cava Bogotá",
        "lat": 4.6126, "lng": -74.0686,
        "descripcion": "De la chef Leonor Espinosa, #23 en Latin America's 50 Best 2025 y Basque Culinary World Prize. Su menú 'Ciclo-Bioma' recorre la biodiversidad colombiana en 20+ momentos; coctelería de su hija sommelier Laura Hernández. Una de las experiencias cumbre de la ciudad.",
        "confianza": "muy_alta",
    },
    {
        "city_display": "Bogotá", "name": "Afluente", "category": "restaurante", "zona": "Chapinero",
        "dir": "Bogotá, Colombia",
        "maps_query": "Afluente restaurante Bogotá",
        "lat": 4.6440, "lng": -74.0628,
        "descripcion": "Debutó en el puesto #34 de Latin America's 50 Best 2025 — uno de los grandes estrenos colombianos del año. Cocina de autor que lee los ríos y ecosistemas del país. Reservar con anticipación.",
        "confianza": "muy_alta",
    },
    {
        "city_display": "Bogotá", "name": "Humo Negro", "category": "restaurante", "zona": "Chapinero Alto",
        "dir": "Carrera 5 #56-06, Chapinero, Bogotá, Colombia",
        "maps_query": "Humo Negro Carrera 5 56-06 Chapinero Bogotá",
        "lat": 4.6404, "lng": -74.0608,
        "descripcion": "#41 en Latin America's 50 Best 2025. Del chef Jaime Torregrosa (ex-El Chato): izakaya que cruza Japón, Latinoamérica y lo nórdico con producto colombiano sostenible (pirarucú amazónico). Carrera 5 #56-06.",
        "confianza": "muy_alta",
    },
    {
        "city_display": "Bogotá", "name": "Oda", "category": "restaurante", "zona": "Zona G",
        "dir": "Carrera 6 #66-46, Bogotá, Colombia",
        "maps_query": "Oda restaurante G Lounge Bogotá",
        "lat": 4.6575, "lng": -74.0588,
        "descripcion": "#76 en la lista extendida de Latin America's 50 Best 2025 y ganador del Sustainable Restaurant Award. Dentro del G Lounge (Zona G): menú contemporáneo de ingredientes autóctonos con foco en sostenibilidad.",
        "confianza": "alta",
    },
    {
        "city_display": "Bogotá", "name": "Criterión", "category": "restaurante", "zona": "La Candelaria / Centro",
        "dir": "Calle 69A #5-75, Bogotá, Colombia",
        "maps_query": "Criterion restaurante Rausch Bogotá",
        "lat": 4.6520, "lng": -74.0598,
        "descripcion": "El buque insignia de los hermanos Rausch, referente de alta cocina franco-colombiana en Bogotá desde hace dos décadas. Elegante, ideal para una cena especial o de negocios. (No hay estrellas Michelin en Colombia; es reconocimiento local e internacional de crítica.)",
        "confianza": "alta",
    },
    {
        "city_display": "Bogotá", "name": "El Cielo Bogotá", "category": "restaurante", "zona": "Zona T / El Retiro",
        "dir": "Carrera 9 #79-04, Bogotá, Colombia",
        "maps_query": "El Cielo restaurante Bogotá",
        "lat": 4.6620, "lng": -74.0560,
        "descripcion": "Experiencia multisensorial del chef Juan Manuel Barrientos (quien obtuvo estrella Michelin con El Cielo en EE. UU.). Menú-degustación con 'momentos' que van más allá del plato. Reservar; ideal para ocasiones.",
        "confianza": "alta",
    },
    {
        "city_display": "Bogotá", "name": "Mesa Franca", "category": "restaurante", "zona": "Chapinero Alto",
        "dir": "Calle 61 #5-56, Chapinero, Bogotá, Colombia",
        "maps_query": "Mesa Franca Calle 61 5-56 Chapinero Bogotá",
        "lat": 4.6480, "lng": -74.0612,
        "descripcion": "Casa de Chapinero muy querida por la crítica local: cocina de autor de raíz criolla y coctelería con viche del Pacífico (la 'Mula Pacífica'). Ambiente relajado, platos para compartir. Calle 61 #5-56.",
        "confianza": "alta",
    },
    {
        "city_display": "Bogotá", "name": "Salvo Patria", "category": "restaurante", "zona": "Chapinero Alto",
        "dir": "Carrera 4 Bis #58-60, Chapinero Alto, Bogotá, Colombia",
        "maps_query": "Salvo Patria Carrera 4 Bis 58-60 Chapinero Bogotá",
        "lat": 4.6395, "lng": -74.0618,
        "descripcion": "Más de 15 años de cocina colombiana de temporada en casa de Chapinero Alto, un clásico moderno de la ciudad. Buen café de día, cocina de producto de noche; platos como los agnolotti de mazorca.",
        "confianza": "alta",
    },
    {
        "city_display": "Bogotá", "name": "Prudencia", "category": "restaurante", "zona": "La Candelaria",
        "dir": "Carrera 2 #11-34, La Candelaria, Bogotá, Colombia",
        "maps_query": "Prudencia restaurante La Candelaria Bogotá",
        "lat": 4.5972, "lng": -74.0712,
        "descripcion": "Joya escondida en un patio colonial de La Candelaria: menú del día corto y cambiante, cocina de horno y producto de mercado. Solo almuerzos entre semana, con reserva — perfecto tras los museos del centro.",
        "confianza": "media_alta",
    },
    {
        "city_display": "Bogotá", "name": "Mini-Mal", "category": "restaurante", "zona": "Chapinero",
        "dir": "Transversal 4 Bis #57-52, Chapinero, Bogotá, Colombia",
        "maps_query": "Mini-Mal restaurante Bogotá",
        "lat": 4.6398, "lng": -74.0635,
        "descripcion": "Cocina que celebra la biodiversidad colombiana y los ingredientes de las regiones (Amazonía, Pacífico, Caribe). Propuesta original y sin pretensiones — una buena entrada a los sabores del país.",
        "confianza": "media_alta",
    },
    {
        "city_display": "Bogotá", "name": "Harry Sasson", "category": "restaurante", "zona": "Zona G",
        "dir": "Carrera 9 #75-70, Bogotá, Colombia",
        "maps_query": "Harry Sasson restaurante Bogotá",
        "lat": 4.6612, "lng": -74.0581,
        "descripcion": "Clásico mayor de la gastronomía bogotana, en una casa espectacular: fusión de técnica europea y producto colombiano, gran servicio. Ideal para una cena elegante o de negocios.",
        "confianza": "alta",
    },

    # ════════════ RESTAURANTES — carne, mar, temáticos ════════════
    {
        "city_display": "Bogotá", "name": "La Cabrera", "category": "restaurante", "zona": "Zona G",
        "dir": "Calle 69A #5-19, Zona G, Bogotá, Colombia",
        "maps_query": "La Cabrera Parrilla Argentina Calle 69A 5-19 Zona G Bogotá",
        "lat": 4.6562, "lng": -74.0566,
        "descripcion": "Parrilla de referencia para cortes de carne al estilo argentino-colombiano. Varias sedes; la de Zona G es la más concurrida. Buen punto para una comida contundente.",
        "confianza": "media_alta",
    },
    {
        "city_display": "Bogotá", "name": "80 Sillas", "category": "restaurante", "zona": "Zona G",
        "dir": "Carrera 6 #70-22, Bogotá, Colombia",
        "maps_query": "80 Sillas cevichería Bogotá",
        "lat": 4.6576, "lng": -74.0592,
        "descripcion": "Cebichería y cocina del Caribe colombiano muy querida: ceviches, cazuelas y pescado fresco en ambiente vibrante. Pequeño y popular — mejor reservar.",
        "confianza": "media_alta",
    },
    {
        "city_display": "Bogotá", "name": "Club Colombia", "category": "restaurante", "zona": "Zona G",
        "dir": "Calle 82 #9-11, Bogotá, Colombia",
        "maps_query": "Restaurante Club Colombia Bogotá",
        "lat": 4.6668, "lng": -74.0552,
        "descripcion": "Alta cocina colombiana tradicional (grupo Crepes & Waffles) en casa elegante: ajiaco, posta, postres criollos. La forma más cómoda de probar lo clásico del país bien ejecutado.",
        "confianza": "alta",
    },
    {
        "city_display": "Bogotá", "name": "Central Cevichería", "category": "restaurante", "zona": "Zona T / El Nogal",
        "dir": "Carrera 13 #85-14, Zona Rosa, Bogotá, Colombia",
        "maps_query": "Central Cevicheria Carrera 13 85-14 Zona Rosa Bogotá",
        "lat": 4.6672, "lng": -74.0535,
        "descripcion": "Cevichería animada y muy popular cerca de la Zona T — ceviches, cocteles y ambiente de tarde-noche. Buena opción informal antes de salir.",
        "confianza": "media",
    },
    {
        "city_display": "Bogotá", "name": "Pizzardi Artigianale", "category": "restaurante", "zona": "Zona G",
        "dir": "Calle 81 #11-17, Bogotá, Colombia",
        "maps_query": "Pizzardi Artigianale Calle 81 11-17 Bogotá",
        "lat": 4.6588, "lng": -74.0588,
        "descripcion": "Pizza napolitana con certificación de la True Neapolitan Pizza Association, considerada de las mejores de Colombia. Masa fermentada, horno de leña.",
        "confianza": "alta",
    },
    {
        "city_display": "Bogotá", "name": "La Lucha Sanguchería", "category": "restaurante", "zona": "Zona G",
        "dir": "Avenida Calle 85 #12-95, Bogotá, Colombia",
        "maps_query": "La Lucha Sangucheria Calle 85 12-95 Bogotá",
        "lat": 4.6560, "lng": -74.0596,
        "descripcion": "Sandwichería peruana informal y rápida — clásico para un almuerzo bueno y económico en Zona G. Varias sedes en la ciudad.",
        "confianza": "media",
    },

    # ════════════ TRADICIONAL E ICÓNICO ════════════
    {
        "city_display": "Bogotá", "name": "La Puerta Falsa", "category": "restaurante", "zona": "La Candelaria",
        "dir": "Calle 11 #6-50, La Candelaria, Bogotá, Colombia",
        "maps_query": "La Puerta Falsa Bogotá",
        "lat": 4.5980, "lng": -74.0745,
        "descripcion": "Centenaria, a pasos de la Plaza de Bolívar — la parada obligada de tamal santafereño y chocolate con almojábana en el centro histórico. Pequeñísima y siempre llena; vale la fila.",
        "confianza": "alta",
    },
    {
        "city_display": "Bogotá", "name": "Restaurante La Puerta de la Tradición (Santa Fe)", "category": "restaurante", "zona": "La Candelaria",
        "dir": "Calle 11 #5-11, La Candelaria, Bogotá, Colombia",
        "maps_query": "La Puerta de la Tradicion restaurante Calle 11 5-11 La Candelaria Bogotá",
        "lat": 4.5969, "lng": -74.0740,
        "descripcion": "Cocina santafereña tradicional en el centro histórico: ajiaco con pollo, mazorca y alcaparras, puchero. Buen plan de almuerzo entre museos.",
        "confianza": "media",
    },
    {
        "city_display": "Bogotá", "name": "Casa Vieja", "category": "restaurante", "zona": "Centro Internacional",
        "dir": "Avenida Jiménez #3-63, Bogotá, Colombia",
        "maps_query": "Restaurante Casa Vieja Bogotá",
        "lat": 4.6018, "lng": -74.0698,
        "descripcion": "Institución de la cocina colombiana tradicional desde 1965: su ajiaco santafereño es de los más reconocidos de la ciudad. Ambiente clásico, ideal para un almuerzo típico.",
        "confianza": "media_alta",
    },
    {
        "city_display": "Bogotá", "name": "Crepes & Waffles", "category": "restaurante", "zona": "Varias sedes",
        "dir": "Calle 85 #11-69, Bogotá, Colombia",
        "maps_query": "Crepes & Waffles Bogotá",
        "lat": 4.6680, "lng": -74.0548,
        "descripcion": "La cadena más querida de Colombia: crepes dulces y salados, ensaladas y helados a muy buen precio, calidad consistente y empleo mayormente a mujeres cabeza de hogar. Apuesta segura en cualquier zona — nunca te van a 'tumbar'.",
        "confianza": "alta",
    },
    {
        "city_display": "Bogotá", "name": "Andrés Carne de Res (Chía)", "category": "restaurante", "zona": "Chía (afueras)",
        "dir": "Calle 3 #11A-56, Chía, Cundinamarca, Colombia",
        "maps_query": "Andrés Carne de Res Chía",
        "lat": 4.8617, "lng": -74.0533,
        "descripcion": "El templo original de la rumba y la carne en Chía, a ~40 min al norte: un universo de decoración, show, baile y comida que hay que vivir una vez. Mejor viernes/sábado en la noche, ir en taxi o app (queda fuera de Bogotá). Su versión urbana es Andrés D.C. en la Zona T.",
        "confianza": "muy_alta",
    },

    # ════════════ CAFÉ DE ESPECIALIDAD Y PANADERÍAS ════════════
    {
        "city_display": "Bogotá", "name": "Tropicalia Coffee", "category": "cafe", "zona": "Chapinero Alto",
        "dir": "Calle 81A #8-23, Bogotá, Colombia",
        "lat": 4.6660, "lng": -74.0558,
        "descripcion": "#9 mundial en The World's 100 Best Coffee Shops 2026 — la cafetería colombiana mejor posicionada del planeta. Métodos V60, Chemex, Aeropress; brunch y catas guiadas. Calle 81A #8-23.",
        "confianza": "muy_alta",
    },
    {
        "city_display": "Bogotá", "name": "Azahar Café", "category": "cafe", "zona": "Quinta Camacho",
        "dir": "Calle 69A #10-44, Bogotá, Colombia",
        "maps_query": "Azahar Café Quinta Camacho Bogotá",
        "lat": 4.6535, "lng": -74.0608,
        "descripcion": "Café de especialidad colombiano consolidado, con tostión propia y trazabilidad de origen. La sede de Quinta Camacho, en casa antigua, es de las más bonitas para sentarse a trabajar o desayunar.",
        "confianza": "alta",
    },
    {
        "city_display": "Bogotá", "name": "Café Cultor", "category": "cafe", "zona": "Chapinero",
        "dir": "Calle 70A #9-44, Quinta Camacho, Bogotá, Colombia",
        "maps_query": "Café Cultor Casa Calle 70A 9-44 Quinta Camacho Bogotá",
        "lat": 4.6520, "lng": -74.0588,
        "descripcion": "Tostador de especialidad con causa social y cafés de origen colombiano bien presentados. Buen lugar para probar un filtrado distinto cada visita.",
        "confianza": "media_alta",
    },
    {
        "city_display": "Bogotá", "name": "Catación Pública", "category": "cafe", "zona": "Quinta Camacho",
        "dir": "Carrera 6 #66-46, Bogotá, Colombia",
        "maps_query": "Catación Pública Bogotá",
        "lat": 4.6555, "lng": -74.0598,
        "descripcion": "Más que un café: hacen catas y talleres para entender el café colombiano de origen. Ideal si quieres aprender a catar, no solo tomar. Reservar la experiencia.",
        "confianza": "media_alta",
    },
    {
        "city_display": "Bogotá", "name": "Libertario Coffee Roasters", "category": "cafe", "zona": "Chapinero",
        "dir": "Calle 70A #5-37, Zona G, Bogotá, Colombia",
        "maps_query": "Libertario Coffee Roasters Calle 70A 5-37 Zona G Bogotá",
        "lat": 4.6418, "lng": -74.0610,
        "descripcion": "Tostador de especialidad con varias barras en la ciudad, fuerte en espresso y métodos. Buena parada de café serio en Chapinero.",
        "confianza": "media",
    },
    {
        "city_display": "Bogotá", "name": "Amor Perfecto", "category": "cafe", "zona": "Usaquén",
        "dir": "Carrera 6 #117-50, Usaquén, Bogotá, Colombia",
        "maps_query": "Amor Perfecto café Carrera 6 117-50 Usaquén Bogotá",
        "lat": 4.6940, "lng": -74.0325,
        "descripcion": "Pionera colombiana del café de especialidad — acercó los cafés premium de origen único al consumidor local. Buena parada cerca del mercado de Usaquén.",
        "confianza": "alta",
    },
    {
        "city_display": "Bogotá", "name": "Colo Coffee", "category": "cafe", "zona": "Parque de la 93 (Chicó)",
        "dir": "Carrera 13 #83-19, Bogotá, Colombia",
        "maps_query": "Colo Coffee Carrera 13 83-19 Bogotá Colombia",
        "lat": 4.6757, "lng": -74.0478,
        "descripcion": "Cafetería de especialidad amplia y con buen WiFi cerca de la Calle 93 — citada en rutas de café de Bogotá como buen sitio para trabajar o pasar la tarde.",
        "confianza": "media_alta",
    },
    {
        "city_display": "Bogotá", "name": "Brot Bakery & Café", "category": "cafe", "zona": "Zona T / El Nogal",
        "dir": "Calle 81 #7-93, Bogotá, Colombia",
        "maps_query": "Brot Bakery Cafe Calle 81 7-93 Bogotá",
        "lat": 4.6648, "lng": -74.0555,
        "descripcion": "Panadería-café clásica desde 1999, famosa por su baguette de chocolate y sus desayunos. Recurrente en los tops de brunch de la ciudad; terraza agradable. Calle 81 #7-93.",
        "confianza": "alta",
    },
    {
        "city_display": "Bogotá", "name": "Masa (Rosales)", "category": "cafe", "zona": "Rosales / Zona G",
        "dir": "Calle 70A #4-83, Bogotá, Colombia",
        "maps_query": "Masa Rosales Calle 70A 4-83 Bogotá",
        "lat": 4.6541, "lng": -74.0563,
        "descripcion": "Panadería y brunch de referencia: croissant de almendras, pan recién horneado, pan de canela. De los mejores desayunos de Bogotá; suele llenarse el fin de semana. Calle 70A #4-83.",
        "confianza": "alta",
    },
    {
        "city_display": "Bogotá", "name": "Árbol del Pan", "category": "cafe", "zona": "Chapinero Alto",
        "dir": "Carrera 4 #66-46, Chapinero, Bogotá, Colombia",
        "maps_query": "Árbol del Pan café Carrera 4 66-46 Chapinero Bogotá",
        "lat": 4.6512, "lng": -74.0570,
        "descripcion": "Panadería artesanal encantadora y destino de brunch muy querido en Chapinero — de las panaderías mejor valoradas de la ciudad. Cra 4 #66-46.",
        "confianza": "media_alta",
    },

    # ════════════ BARES Y VIDA NOCTURNA ════════════
    {
        "city_display": "Bogotá", "name": "Andrés D.C.", "category": "bar", "zona": "Zona T / Zona Rosa",
        "dir": "Calle 82 #12-21, Bogotá, Colombia",
        "maps_query": "Andrés DC Calle 82 Zona T Bogotá",
        "lat": 4.6680, "lng": -74.0533,
        "descripcion": "La sede urbana del mítico Andrés Carne de Res, en plena Zona Rosa: cuatro pisos de restaurante, bar y baile. Uno de los planes nocturnos más emblemáticos y seguros para empezar la noche en Bogotá.",
        "confianza": "muy_alta",
    },
    {
        "city_display": "Bogotá", "name": "Gaira Café Cumbia House", "category": "bar", "zona": "Chicó / Calle 96",
        "dir": "Carrera 13 #96-11, Bogotá, Colombia",
        "maps_query": "Gaira Café Cumbia House Bogotá",
        "lat": 4.6840, "lng": -74.0480,
        "descripcion": "El bar-restaurante de la familia Vives: música en vivo, vallenato y cumbia, comida del Caribe colombiano. Plan de noche muy colombiano y festivo; mejor reservar fin de semana.",
        "confianza": "alta",
    },
    {
        "city_display": "Bogotá", "name": "Theatron", "category": "bar", "zona": "Chapinero",
        "dir": "Calle 58 #10-32, Chapinero, Bogotá, Colombia",
        "maps_query": "Theatron de Película Bogotá",
        "lat": 4.6395, "lng": -74.0648,
        "descripcion": "La discoteca LGBTIQ+ más grande de Latinoamérica: múltiples salas, cada una con su género musical, en un antiguo teatro de Chapinero. Ícono de la noche bogotana. Entrada con barra incluida; ir en grupo y en taxi/app.",
        "confianza": "alta",
    },
    {
        "city_display": "Bogotá", "name": "Armando Records", "category": "bar", "zona": "Zona T",
        "dir": "Calle 85 #14-46, Bogotá, Colombia",
        "maps_query": "Armando Records Bogotá",
        "lat": 4.6688, "lng": -74.0548,
        "descripcion": "Bar y terraza icónica de la Calle 85: música alternativa e indie, rooftop con ambiente. Punto de referencia de la rumba joven en la Zona T.",
        "confianza": "media_alta",
    },
    {
        "city_display": "Bogotá", "name": "Baum", "category": "bar", "zona": "Centro / Las Nieves",
        "dir": "Calle 33 #6-24, Bogotá, Colombia",
        "maps_query": "Baum club Bogotá",
        "lat": 4.6210, "lng": -74.0678,
        "descripcion": "El club de música electrónica de referencia en Bogotá (techno y house), con line-ups internacionales. Para quien busca rumba electrónica de verdad. Abre tarde; ir en app.",
        "confianza": "media_alta",
    },
    {
        "city_display": "Bogotá", "name": "Video Club", "category": "bar", "zona": "Chapinero",
        "dir": "Calle 64 #13-09, Chapinero, Bogotá, Colombia",
        "maps_query": "Video Club bar Calle 64 13-09 Chapinero Bogotá",
        "lat": 4.6560, "lng": -74.0560,
        "descripcion": "Club de electrónica querido por la escena local, ambiente alternativo y buenos DJs. Plan de noche para bailar sin pretensiones.",
        "confianza": "media",
    },
    {
        "city_display": "Bogotá", "name": "Apache Rooftop (Click Clack)", "category": "bar", "zona": "Zona G",
        "dir": "Carrera 11 #93-77, Bogotá, Colombia",
        "maps_query": "Apache Rooftop Click Clack Hotel Bogotá",
        "lat": 4.6760, "lng": -74.0488,
        "descripcion": "Bar en la azotea del hotel Click Clack con vista de la ciudad y los cerros — cocteles al atardecer. Plan más relajado y fotogénico que la discoteca. Cerca del Parque de la 93.",
        "confianza": "media_alta",
    },
    {
        "city_display": "Bogotá", "name": "Galería Café Libro", "category": "bar", "zona": "Zona T / Centro",
        "dir": "Carrera 11A #93-42, Parque de la 93, Bogotá, Colombia",
        "maps_query": "Galería Café Libro Carrera 11A 93-42 Parque 93 Bogotá",
        "lat": 4.6662, "lng": -74.0540,
        "descripcion": "Templo de la salsa en Bogotá: pista, orquesta y baile hasta tarde. Si quieres salsa de verdad, este es el sitio. Hay sede en la Zona T y en el centro.",
        "confianza": "media_alta",
    },
    {
        "city_display": "Bogotá", "name": "Bogotá Beer Company (BBC)", "category": "bar", "zona": "Varias sedes",
        "dir": "Carrera 12 #83-33, Bogotá, Colombia",
        "maps_query": "Bogotá Beer Company Zona T",
        "lat": 4.6678, "lng": -74.0538,
        "descripcion": "La cervecería artesanal más conocida de la ciudad, con sedes por toda Bogotá. Plan informal y seguro para unas cervezas; buena parada antes de salir de rumba.",
        "confianza": "media_alta",
    },
    {
        "city_display": "Bogotá", "name": "Taninos Park Wines", "category": "bar", "zona": "Parkway / La Soledad",
        "dir": "Calle 39 #21-19, La Soledad, Bogotá, Colombia",
        "maps_query": "Taninos Park Wines Calle 39 21-19 Parkway Bogotá",
        "lat": 4.6285, "lng": -74.0725,
        "descripcion": "Bar de vinos en el Parkway con más de 140 etiquetas del mundo — plan tranquilo de copas en una zona bohemia y caminable. Buen panorama de noche relajada.",
        "confianza": "media",
    },
    {
        "city_display": "Bogotá", "name": "Zona T / Zona Rosa (vida nocturna)", "category": "atraccion", "zona": "Zona T / Zona Rosa",
        "dir": "Calle 82 con Carrera 13, Bogotá, Colombia",
        "maps_query": "Zona T Bogotá",
        "lat": 4.6678, "lng": -74.0528,
        "descripcion": "Calles peatonales (la 'T') con la mayor concentración de bares y discotecas de la ciudad, entre carreras 11-15 y calles 82-86. Zona segura y animada para arrancar la noche.",
        "confianza": "alta",
    },

    # ════════════ MUSEOS Y CULTURA ════════════
    {
        "city_display": "Bogotá", "name": "Museo del Oro", "category": "atraccion", "zona": "La Candelaria / Centro",
        "dir": "Carrera 6 #15-88, Bogotá, Colombia",
        "maps_query": "Museo del Oro Bogotá",
        "lat": 4.6019, "lng": -74.0721,
        "descripcion": "Una de las colecciones de orfebrería prehispánica más importantes del mundo. Imprescindible; entrada económica y gratis los domingos. Dedícale 1.5–2 horas.",
        "confianza": "muy_alta",
    },
    {
        "city_display": "Bogotá", "name": "Museo Botero", "category": "atraccion", "zona": "La Candelaria",
        "dir": "Calle 11 #4-41, La Candelaria, Bogotá, Colombia",
        "maps_query": "Museo Botero Bogotá",
        "lat": 4.5973, "lng": -74.0726,
        "descripcion": "Colección donada por Fernando Botero en casa colonial: obras suyas más Picasso, Dalí, Monet y Renoir. Entrada GRATUITA. A 10 min a pie del Museo del Oro.",
        "confianza": "muy_alta",
    },
    {
        "city_display": "Bogotá", "name": "Museo Nacional de Colombia", "category": "atraccion", "zona": "Centro Internacional",
        "dir": "Carrera 7 #28-66, Bogotá, Colombia",
        "maps_query": "Museo Nacional de Colombia",
        "lat": 4.6155, "lng": -74.0686,
        "descripcion": "El museo más antiguo del país, en un edificio que fue penitenciaría (El Panóptico): historia, arqueología y arte colombiano. Buen plan de medio día; gratis los domingos.",
        "confianza": "alta",
    },
    {
        "city_display": "Bogotá", "name": "MAMBO — Museo de Arte Moderno", "category": "atraccion", "zona": "Centro Internacional",
        "dir": "Calle 24 #6-00, Bogotá, Colombia",
        "maps_query": "Museo de Arte Moderno de Bogotá MAMBO",
        "lat": 4.6122, "lng": -74.0690,
        "descripcion": "Arte moderno y contemporáneo latinoamericano en un edificio de Rogelio Salmona. Exposiciones rotativas; buena parada cultural en el Centro Internacional.",
        "confianza": "media_alta",
    },
    {
        "city_display": "Bogotá", "name": "Quinta de Bolívar", "category": "atraccion", "zona": "La Candelaria / Cerros",
        "dir": "Calle 20 #2-91 Este, Bogotá, Colombia",
        "maps_query": "Casa Museo Quinta de Bolívar Bogotá",
        "lat": 4.6029, "lng": -74.0668,
        "descripcion": "Casa-museo donde vivió Simón Bolívar, con jardines a los pies de Monserrate. Tranquila y bonita; combínala con la subida a Monserrate.",
        "confianza": "alta",
    },
    {
        "city_display": "Bogotá", "name": "Biblioteca Luis Ángel Arango", "category": "atraccion", "zona": "La Candelaria",
        "dir": "Calle 11 #4-14, La Candelaria, Bogotá, Colombia",
        "maps_query": "Biblioteca Luis Ángel Arango Bogotá",
        "lat": 4.5969, "lng": -74.0731,
        "descripcion": "Una de las bibliotecas más importantes de Latinoamérica, con salas de exposición y conciertos. Arquitectura notable; entrada libre a buena parte de sus espacios.",
        "confianza": "media_alta",
    },
    {
        "city_display": "Bogotá", "name": "Teatro Colón", "category": "atraccion", "zona": "La Candelaria",
        "dir": "Calle 10 #5-32, La Candelaria, Bogotá, Colombia",
        "maps_query": "Teatro Colón Bogotá",
        "lat": 4.5966, "lng": -74.0739,
        "descripcion": "El teatro nacional, joya del siglo XIX restaurada con techos pintados a mano. Vale la pena un tour guiado o una función. En pleno centro histórico.",
        "confianza": "media_alta",
    },
    {
        "city_display": "Bogotá", "name": "Maloka", "category": "atraccion", "zona": "Salitre",
        "dir": "Carrera 68D #24A-51, Bogotá, Colombia",
        "maps_query": "Maloka Bogotá",
        "lat": 4.6585, "lng": -74.1010,
        "descripcion": "Centro interactivo de ciencia y tecnología con cine domo — plan ideal en familia o con niños, sobre todo si el clima no acompaña.",
        "confianza": "media",
    },
    {
        "city_display": "Bogotá", "name": "Plaza de Bolívar", "category": "atraccion", "zona": "La Candelaria",
        "dir": "Carrera 7 #11-10, La Candelaria, Bogotá, Colombia",
        "maps_query": "Plaza de Bolívar Bogotá",
        "lat": 4.5981, "lng": -74.0760,
        "descripcion": "Corazón histórico y político: Catedral Primada, Capitolio y Palacio de Justicia alrededor. Punto de partida natural del recorrido por La Candelaria.",
        "confianza": "muy_alta",
    },
    {
        "city_display": "Bogotá", "name": "Chorro de Quevedo", "category": "atraccion", "zona": "La Candelaria",
        "dir": "Calle 12B #2-00, La Candelaria, Bogotá, Colombia",
        "maps_query": "Chorro de Quevedo Bogotá",
        "lat": 4.5970, "lng": -74.0699,
        "descripcion": "Plazoleta donde la tradición ubica la fundación de Bogotá: ambiente bohemio, cuenteros, chicha y arte urbano. De día es seguro y encantador; de noche, ir acompañado.",
        "confianza": "alta",
    },
    {
        "city_display": "Bogotá", "name": "Tour de grafiti por La Candelaria", "category": "experiencia", "zona": "La Candelaria",
        "dir": "Plaza de los Periodistas, La Candelaria, Bogotá, Colombia",
        "maps_query": "Bogotá Graffiti Tour La Candelaria",
        "lat": 4.5996, "lng": -74.0719,
        "descripcion": "Recorrido a pie por el arte urbano del centro — Bogotá es referente mundial de grafiti. Tours por propinas salen a diario; la mejor intro al barrio histórico y su historia reciente.",
        "confianza": "alta",
    },

    # ════════════ MIRADORES Y NATURALEZA ════════════
    {
        "city_display": "Bogotá", "name": "Cerro de Monserrate", "category": "mirador", "zona": "La Candelaria / Cerros",
        "dir": "Carrera 2 Este #21-48, Bogotá, Colombia",
        "maps_query": "Cerro de Monserrate Bogotá",
        "lat": 4.6058, "lng": -74.0557,
        "descripcion": "El mirador insignia de Bogotá (3.152 m): funicular o teleférico, santuario, vista de toda la sabana. Ideal al atardecer entre semana (menos fila). Plan del día 1.",
        "confianza": "muy_alta",
    },
    {
        "city_display": "Bogotá", "name": "Mirador Torre Colpatria", "category": "mirador", "zona": "Centro Internacional",
        "dir": "Carrera 7 #24-89, Bogotá, Colombia",
        "maps_query": "Torre Colpatria mirador Bogotá",
        "lat": 4.6125, "lng": -74.0703,
        "descripcion": "Vista de 360° desde 196 m en el primer rascacielos de la ciudad. Abre solo viernes, sábado, domingo y festivos — confirma horario antes de ir.",
        "confianza": "alta",
    },
    {
        "city_display": "Bogotá", "name": "Quebrada La Vieja (senderismo)", "category": "experiencia", "zona": "Chapinero Alto / Cerros",
        "dir": "Carrera 1 con Calle 71, Bogotá, Colombia",
        "maps_query": "Sendero Quebrada La Vieja Bogotá",
        "lat": 4.6470, "lng": -74.0428,
        "descripcion": "Caminata matutina por los cerros orientales, dentro de la ciudad: bosque, agua y vistas. Abre temprano (cierra ~10–11 am) y suele requerir registro; gratis. Plan local muy querido para arrancar el día.",
        "confianza": "media_alta",
    },
    {
        "city_display": "Bogotá", "name": "Parque El Virrey", "category": "parque", "zona": "El Nogal / Chicó",
        "dir": "Calle 88 con Carrera 15, Bogotá, Colombia",
        "maps_query": "Parque El Virrey Bogotá",
        "lat": 4.6720, "lng": -74.0520,
        "descripcion": "Corredor verde lineal a lo largo de una quebrada en el norte — para caminar, trotar o pasear. Rodeado de cafés y restaurantes; muy seguro de día.",
        "confianza": "media_alta",
    },
    {
        "city_display": "Bogotá", "name": "Parque Nacional Enrique Olaya Herrera", "category": "parque", "zona": "La Macarena / Centro",
        "dir": "Carrera 7 con Calle 36, Bogotá, Colombia",
        "maps_query": "Parque Nacional Bogotá",
        "lat": 4.6195, "lng": -74.0660,
        "descripcion": "El parque urbano histórico de Bogotá, entre el centro y los cerros — senderos, fuentes y zonas verdes. Bonito de día, cerca de La Macarena y sus restaurantes.",
        "confianza": "media",
    },
    {
        "city_display": "Bogotá", "name": "Jardín Botánico José Celestino Mutis", "category": "parque", "zona": "Salitre",
        "dir": "Calle 63 #68-95, Bogotá, Colombia",
        "maps_query": "Jardín Botánico de Bogotá",
        "lat": 4.6685, "lng": -74.0995,
        "descripcion": "El jardín botánico de la ciudad: lago, palmetum, plantas de páramo y bosque andino. Plan tranquilo de medio día, ideal con buen clima.",
        "confianza": "alta",
    },
    {
        "city_display": "Bogotá", "name": "Parque Metropolitano Simón Bolívar", "category": "parque", "zona": "Salitre",
        "dir": "Calle 53 con Carrera 48, Bogotá, Colombia",
        "maps_query": "Parque Simón Bolívar Bogotá",
        "lat": 4.6580, "lng": -74.0930,
        "descripcion": "El gran pulmón verde de Bogotá — lago, ciclorrutas y sede de grandes conciertos y festivales. Para caminar, hacer picnic o ejercicio.",
        "confianza": "alta",
    },
    {
        "city_display": "Bogotá", "name": "Parque de la 93", "category": "parque", "zona": "Parque de la 93 (Chicó)",
        "dir": "Calle 93A con Carrera 13, Bogotá, Colombia",
        "maps_query": "Parque de la 93 Bogotá",
        "lat": 4.6766, "lng": -74.0487,
        "descripcion": "Parque rodeado de restaurantes, cafés y terrazas — plan tranquilo al aire libre o tarde de fotos, con eventos los fines de semana. Una de las zonas más seguras y agradables del norte.",
        "confianza": "muy_alta",
    },

    # ════════════ MERCADOS, LOCAL Y COMPRAS ════════════
    {
        "city_display": "Bogotá", "name": "Mercado de pulgas de Usaquén", "category": "mercado", "zona": "Usaquén",
        "dir": "Carrera 5 con Calle 119B, Usaquén, Bogotá, Colombia",
        "maps_query": "Mercado de las Pulgas Usaquén",
        "lat": 4.6952, "lng": -74.0312,
        "descripcion": "Mercado de antigüedades y artesanías, especialmente los domingos, en el casco colonial de Usaquén. Combínalo con brunch en la zona y la plaza.",
        "confianza": "alta",
    },
    {
        "city_display": "Bogotá", "name": "Plaza de Mercado de Paloquemao", "category": "mercado", "zona": "Paloquemao",
        "dir": "Carrera 25 #19-20, Bogotá, Colombia",
        "maps_query": "Plaza de Mercado Paloquemao Bogotá",
        "lat": 4.6155, "lng": -74.0850,
        "descripcion": "La plaza de mercado más famosa de Bogotá: frutas exóticas, flores, hierbas y desayuno entre puestos. Ir temprano (antes de 9 am); experiencia gastronómica imperdible. Hay tours de frutas.",
        "confianza": "alta",
    },
    {
        "city_display": "Bogotá", "name": "Plaza de Mercado La Perseverancia", "category": "mercado", "zona": "La Perseverancia / Centro",
        "dir": "Calle 30A #5-09, Bogotá, Colombia",
        "maps_query": "Plaza La Perseverancia Bogotá",
        "lat": 4.6178, "lng": -74.0668,
        "descripcion": "Plaza tradicional famosa por sus almuerzos caseros: sancocho, lechona, tamal. Auténtico y económico, muy local. Mejor al mediodía; barrio sencillo, ir de día.",
        "confianza": "media_alta",
    },
    {
        "city_display": "Bogotá", "name": "Centro Comercial Andino", "category": "compras", "zona": "Zona T",
        "dir": "Carrera 11 #82-71, Bogotá, Colombia",
        "maps_query": "Centro Comercial Andino Bogotá",
        "lat": 4.6669, "lng": -74.0540,
        "descripcion": "El mall de referencia de la Zona T: marcas internacionales y colombianas, conectado con El Retiro y Atlantis. Cómodo y seguro, con buena oferta de comida.",
        "confianza": "media_alta",
    },
    {
        "city_display": "Bogotá", "name": "Hacienda Santa Bárbara", "category": "compras", "zona": "Usaquén",
        "dir": "Carrera 7 #115-60, Usaquén, Bogotá, Colombia",
        "maps_query": "Hacienda Santa Bárbara Bogotá",
        "lat": 4.6970, "lng": -74.0335,
        "descripcion": "Centro comercial en una hacienda colonial del siglo XVIII — mezcla de tiendas y patrimonio. Bonito para pasear cerca de Usaquén.",
        "confianza": "media",
    },
    {
        "city_display": "Bogotá", "name": "Artesanías de Colombia (San Diego)", "category": "compras", "zona": "Centro Internacional",
        "dir": "Carrera 10 #26-50 (Plazoleta San Diego), Bogotá, Colombia",
        "maps_query": "Artesanías de Colombia San Diego Bogotá",
        "lat": 4.6118, "lng": -74.0688,
        "descripcion": "Tienda oficial de artesanías colombianas: mochilas wayúu, sombreros, cerámica, hamacas — con precio justo y origen garantizado. La forma segura de comprar artesanía auténtica sin que te 'tumben'.",
        "confianza": "media_alta",
    },

    # ════════════ EXPERIENCIAS LOCALES ════════════
    {
        "city_display": "Bogotá", "name": "Ciclovía dominical", "category": "experiencia", "zona": "Toda la ciudad",
        "dir": "Carrera 7 / Calle 26, Bogotá, Colombia",
        "maps_query": "Ciclovía Bogotá Carrera 7",
        "lat": 4.6280, "lng": -74.0660,
        "descripcion": "Domingos y festivos 7am–2pm, más de 120 km de vías se cierran a los carros para ciclistas, patinadores y peatones. Gratis y muy bogotano — alquila una bici y recorre la Carrera 7 o la Calle 26.",
        "confianza": "muy_alta",
    },
    {
        "city_display": "Bogotá", "name": "Tour en bici por Bogotá", "category": "experiencia", "zona": "La Candelaria / Centro",
        "dir": "La Candelaria, Bogotá, Colombia",
        "maps_query": "Bogotá Bike Tours La Candelaria",
        "lat": 4.5985, "lng": -74.0715,
        "descripcion": "Recorridos guiados en bicicleta que conectan el centro, plazas de mercado, grafiti y barrios — la mejor forma de entender la ciudad en medio día. Operadores reconocidos salen de La Candelaria.",
        "confianza": "media_alta",
    },

    # ════════════ EXCURSIONES (día completo, fuera de la ciudad) ════════════
    {
        "city_display": "Bogotá", "name": "Catedral de Sal de Zipaquirá", "category": "excursion", "zona": "Zipaquirá (afueras)",
        "dir": "Zipaquirá, Cundinamarca, Colombia",
        "maps_query": "Catedral de Sal de Zipaquirá",
        "lat": 5.0190, "lng": -73.9905,
        "descripcion": "Catedral subterránea excavada en una mina de sal, a ~1.5 h al norte. Día completo (combínala con el pueblo de Zipaquirá). Una de las excursiones clásicas desde Bogotá; hay tours o tren turístico los fines de semana.",
        "confianza": "alta",
    },
    {
        "city_display": "Bogotá", "name": "Laguna de Guatavita", "category": "excursion", "zona": "Guatavita / Sesquilé (afueras)",
        "dir": "Sesquilé, Cundinamarca, Colombia",
        "maps_query": "Laguna de Guatavita",
        "lat": 5.0277, "lng": -73.7766,
        "descripcion": "La laguna sagrada muisca, origen de la leyenda de El Dorado, a ~1.5–2 h. Recorrido guiado por la reserva con vistas hermosas. Combínala con el pueblo de Guatavita.",
        "confianza": "media_alta",
    },
    {
        "city_display": "Bogotá", "name": "Mina de Sal de Nemocón", "category": "excursion", "zona": "Nemocón (afueras)",
        "dir": "Nemocón, Cundinamarca, Colombia",
        "maps_query": "Mina de Sal de Nemocón",
        "lat": 5.0680, "lng": -73.8790,
        "descripcion": "Alternativa más íntima y menos turística que Zipaquirá: túneles de sal con espejos de agua espectaculares (allí se grabó parte de la película '33'). Buen plan de día completo al norte.",
        "confianza": "media",
    },
    {
        "city_display": "Bogotá", "name": "Villa de Leyva", "category": "excursion", "zona": "Boyacá (afueras)",
        "dir": "Villa de Leyva, Boyacá, Colombia",
        "maps_query": "Villa de Leyva Boyacá",
        "lat": 5.6339, "lng": -73.5266,
        "descripcion": "Pueblo colonial con una de las plazas empedradas más grandes de América, a ~3.5–4 h. Ideal para quedarse una noche: viñedos, fósiles, desierto de la Candelaria. Plan de fin de semana, no de día.",
        "confianza": "media_alta",
    },
    {
        "city_display": "Bogotá", "name": "Parque Natural Chicaque", "category": "excursion", "zona": "San Antonio del Tequendama (afueras)",
        "dir": "San Antonio del Tequendama, Cundinamarca, Colombia",
        "maps_query": "Parque Natural Chicaque",
        "lat": 4.6090, "lng": -74.3050,
        "descripcion": "Bosque de niebla a ~1 h al suroccidente: senderos, cabañas en los árboles y aire de páramo. Naturaleza de verdad sin alejarse mucho; ideal para un día de caminata.",
        "confianza": "media",
    },
    {
        "city_display": "Bogotá", "name": "Suesca (senderismo y escalada)", "category": "excursion", "zona": "Suesca (afueras)",
        "dir": "Suesca, Cundinamarca, Colombia",
        "maps_query": "Rocas de Suesca",
        "lat": 5.1030, "lng": -73.7960,
        "descripcion": "La meca de la escalada en roca cerca de Bogotá (~1.5 h), con paredes a la orilla del río y rutas para principiantes. También se puede solo caminar. Plan de aventura de un día.",
        "confianza": "media",
    },
]

# Fallback para entradas futuras sin lat/lng propios cuya zona tampoco esté en
# geo.ZONE_COORDS.
_ZIPAQUIRA = (5.0190, -73.9905)


def all_seeds() -> list[dict]:
    """Resuelve lat/lng de cada lugar.

    Prioridad: 1) lat/lng propios. 2) si faltan, fallback al punto de la zona
    (geo.zone_coords) o a _ZIPAQUIRA. El campo `dir` (dirección) y `maps_query`
    se conservan en el dict; db.seed_destination_places ignora las claves que no
    sean columnas, así que no estorban.
    """
    out = []
    for p in BOGOTA:
        if "lat" in p and "lng" in p:
            lat, lng = p["lat"], p["lng"]
        elif "afuera" in p["zona"].lower() or "boyac" in p["zona"].lower():
            lat, lng = _ZIPAQUIRA
        else:
            coords = geo.zone_coords(p["city_display"], p["zona"])
            lat, lng = coords if coords else (0.0, 0.0)
        out.append({**p, "lat": lat, "lng": lng})
    return out
