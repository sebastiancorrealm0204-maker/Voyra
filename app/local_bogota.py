"""Conocimiento LOCAL por lugar de Bogotá → lo que un local te diría parado en la puerta.

Por qué existe este archivo (lee esto antes de tocarlo):
─────────────────────────────────────────────────────────────────────────────
El campo `descripcion` de seed_data responde la pregunta de Wikipedia: "¿qué ES
este lugar?". Eso lo sabe ChatGPT. Lo que hace sentir a un LOCAL son OTRAS
preguntas que el visitante tiene parado en la puerta y que no salen en Google:

  • ¿Qué pido aquí? (no la carta entera — EL plato)
  • ¿A qué hora vengo para no hacer fila / no toparme el tour bus?
  • ¿Cuál es la trampa? (lo que decepciona, el cargo escondido, la sede mala)
  • El dato que solo sabes estando ahí.
  • Lo práctico: reserva, efectivo, cuánto cuesta, cuánto tiempo toma.

Este módulo es ese conocimiento, indexado por NOMBRE (igual que aparece en
seed_data.BOGOTA["name"]). seed_data lo pega a cada lugar como `p["local"]`, y
context.py lo inyecta al prompt para que el Companion LIDERE con el dato local.

Esquema de cada entrada (todos los campos son OPCIONALES — pon solo lo que aporta):
  clave    : str  — la frase de un local en 5 segundos. El "move". Lo más importante.
  pedir    : str  — qué pedir (comida/café/trago/mercado). EL plato, no la carta.
  ver      : str  — qué no perderte (atracción/parque/experiencia/excursión).
  momento  : str  — mejor hora, cómo evitar fila/multitud, cuándo NO ir, reserva.
  ojo      : str  — la trampa / el cuidado / lo que decepciona / el cargo escondido.
  dato     : str  — el dato local que no sale en la descripción de Google.
  practico : str  — pago, reserva, rango de precio, cuánto tiempo, cómo llegar.

⚠️ NOTA PARA EL FUNDADOR — capa crítica de confianza:
Los campos `pedir` están anclados a platos insignia conocidos o a lo que ya dice
la descripción curada. Los campos `ojo` y `dato` son donde más valor hay y donde
más conviene tu verificación de campo (tú conoces Bogotá). Trátalos como un
borrador fuerte que debes confirmar/afinar, NO como verdad absoluta. La regla
anti-alucinación del producto exige que lo que el Companion afirma sea cierto:
si dudas de un dato, bórralo o corrígelo. Es preferible un campo vacío a uno falso.
─────────────────────────────────────────────────────────────────────────────
"""

LOCAL_BOGOTA: dict[str, dict] = {

    # ════════════════════════ ALTA COCINA / FINE DINING ════════════════════════
    "El Chato": {
        "clave": "Es el #1 de Latinoamérica: no es un sitio para improvisar, es un plan que se agenda.",
        "pedir": "El menú degustación es la forma de vivirlo; déjate llevar por la temporada en vez de pedir a la carta.",
        "momento": "Reserva con SEMANAS de anticipación — no esperes mesa el mismo día. Cena, no almuerzo.",
        "ojo": "No es para una comida casual ni para ir con prisa: es una experiencia larga y de gama alta. Si buscas algo rápido o económico, este no es.",
        "practico": "Reserva online obligatoria. Precio alto (degustación). Chapinero Alto, fácil en app de carro.",
    },
    "Leo Cocina y Cava": {
        "clave": "El templo de Leonor Espinosa: aquí se viene a hacer el menú 'Ciclo-Bioma', no a picar.",
        "pedir": "El menú degustación Ciclo-Bioma con el maridaje — es la experiencia completa, ingredientes de toda Colombia.",
        "momento": "Reserva con anticipación. Bloquéale 2.5–3 horas, no lo metas antes de otro plan.",
        "ojo": "Es una de las cumbres de la ciudad y se paga como tal. Ve con tiempo y con hambre.",
        "practico": "Reserva online. Gama alta. Centro Internacional / Chapinero.",
    },
    "Afluente": {
        "clave": "El gran estreno colombiano del año (#34): cocina de autor que 'lee los ríos' del país.",
        "pedir": "Déjate guiar por el menú de temporada; la propuesta cambia con el ecosistema que estén trabajando.",
        "momento": "Reserva con anticipación — es nuevo y muy pedido.",
        "practico": "Reserva online. Gama alta. Chapinero.",
    },
    "Humo Negro": {
        "clave": "Izakaya de un ex-El Chato: Japón cruzado con Amazonía. Aquí se viene a compartir y picar.",
        "pedir": "Platos para compartir; pregunta por lo que lleve pirarucú (pescado amazónico) — es su sello.",
        "momento": "Mejor de noche. Reserva fin de semana.",
        "ojo": "Es de compartir: pide varias cosas entre todos en vez de un plato por persona.",
        "practico": "Carrera 5 #56-06, Chapinero Alto. Gama media-alta.",
    },
    "Oda": {
        "clave": "Cocina sostenible premiada dentro del G Lounge, en plena Zona G.",
        "pedir": "El menú contemporáneo de temporada; ingredientes autóctonos es el eje.",
        "momento": "Reserva recomendada, sobre todo de noche.",
        "practico": "Dentro del G Lounge, Zona G. Gama alta.",
    },
    "Criterión": {
        "clave": "El clásico de los hermanos Rausch: alta cocina franco-colombiana para una cena que importa.",
        "pedir": "Sus platos insignia franco-colombianos; es el sitio para una ocasión especial o cena de negocios.",
        "momento": "Reserva. Ideal de noche.",
        "ojo": "Es elegante de verdad — código de vestir cuidado. No es plan de jeans y prisa.",
        "practico": "La Candelaria / Centro. Gama alta.",
    },
    "El Cielo Bogotá": {
        "clave": "Experiencia multisensorial con 'momentos': no vienes a comer, vienes a que te sorprendan.",
        "pedir": "El menú-degustación completo — la gracia es la secuencia de momentos, no elegir platos.",
        "momento": "Reserva. Bloquea la noche entera, es largo.",
        "ojo": "Esperas un show gastronómico, no una cena normal. Si quieres comer y ya, no es para ti.",
        "practico": "Zona G. Gama alta. Reserva online.",
    },
    "Harry Sasson": {
        "clave": "Casa espectacular y clásico mayor: la apuesta segura para impresionar en una cena.",
        "pedir": "Técnica europea con producto colombiano; bueno para cena elegante o de negocios.",
        "momento": "Reserva, sobre todo de noche.",
        "practico": "Zona G. Gama alta.",
    },

    # ════════════════════════ COCINA DE AUTOR / TERRITORIO ════════════════════════
    "Mesa Franca": {
        "clave": "Casa querida de Chapinero: cocina criolla de autor y coctelería con viche del Pacífico.",
        "pedir": "Platos para compartir y el coctel 'Mula Pacífica' (lleva viche). Relajado, no es de etiqueta.",
        "momento": "Reserva fin de semana. Buena para una noche tranquila con tragos.",
        "practico": "Calle 61 #5-56, Chapinero Alto. Gama media-alta.",
    },
    "Salvo Patria": {
        "clave": "Un clásico moderno de Chapinero: café de día, cocina de producto de noche.",
        "pedir": "De día, buen café; de noche, platos de temporada como los agnolotti de mazorca.",
        "momento": "Ideal para almuerzo relajado o noche tranquila. Reserva si vas en grupo.",
        "dato": "Funciona distinto según la hora — no esperes la misma carta a media mañana que en la cena.",
        "practico": "Chapinero Alto. Gama media. Tel.: +57 601 702-6367.",
    },
    "Prudencia": {
        "clave": "Joya escondida en un patio colonial: el mejor premio después de los museos del centro.",
        "pedir": "El menú del día (corto y cambiante), cocina de horno y producto de mercado.",
        "momento": "SOLO almuerzos entre semana y CON RESERVA. Si caes sin reservar, probablemente no entras.",
        "ojo": "No abre noches ni fines de semana. Mucha gente llega y se decepciona por no saber esto.",
        "practico": "La Candelaria. Reserva obligatoria. Gama media-alta.",
    },
    "Mini-Mal": {
        "clave": "La mejor puerta de entrada a los sabores de las regiones de Colombia, sin pretensiones.",
        "pedir": "Lo que celebre Amazonía, Pacífico o Caribe en la carta — ahí está la gracia.",
        "momento": "Bueno para almuerzo o cena tranquila.",
        "practico": "Chapinero. Gama media.",
    },
    "Tábula": {
        "clave": "Brasa, horno de leña y mesas largas para compartir: el plan ideal para grupos.",
        "pedir": "Lo que salga de la brasa y del horno de leña, producto de temporada, impronta mediterránea.",
        "momento": "Perfecto en grupo. Reserva si son varios.",
        "practico": "Zona G. Gama media-alta. Ambiente informal y cálido.",
    },
    "Oci.Mde": {
        "clave": "Cocina paisa de autor — el sabor antioqueño revisado con técnica, querido en Chapinero Alto.",
        "pedir": "Sus versiones de autor de la cocina paisa; consistencia es su fuerte.",
        "momento": "Reserva recomendada.",
        "practico": "Chapinero Alto. Gama media.",
    },

    # ════════════════════════ TÍPICO / SANTAFEREÑO ════════════════════════
    "La Puerta Falsa": {
        "clave": "La parada obligada del centro histórico: tamal santafereño y chocolate con almojábana. Vale la fila.",
        "pedir": "Tamal santafereño + chocolate completo con almojábana. El combo clásico de Bogotá de toda la vida.",
        "momento": "Siempre hay fila y es pequeñísima — ve a media mañana o media tarde, evita el pico del almuerzo.",
        "ojo": "El chocolate se toma 'mojando' el queso/almojábana dentro: no es error, es la costumbre santafereña.",
        "dato": "Es centenaria, a pasos de la Plaza de Bolívar. Espacios diminutos: probablemente comas codo a codo, es parte del encanto.",
        "practico": "La Candelaria. Económico. Efectivo a la mano.",
    },
    "Restaurante La Puerta de la Tradición (Santa Fe)": {
        "clave": "Almuerzo santafereño entre museos: ajiaco de verdad sin salir del centro.",
        "pedir": "Ajiaco con pollo, mazorca y alcaparras (con crema y aguacate aparte); o el puchero.",
        "momento": "Plan de almuerzo. Encaja perfecto entre el Museo del Oro y el Botero.",
        "practico": "La Candelaria. Económico-medio.",
    },
    "Casa Vieja": {
        "clave": "Institución desde 1965: su ajiaco es de los más reconocidos de la ciudad.",
        "pedir": "El ajiaco santafereño — es por lo que la gente viene. Llega con sus acompañamientos (crema, alcaparras, aguacate).",
        "momento": "Almuerzo típico. Ambiente clásico, sin prisa.",
        "dato": "El ajiaco se arma a tu gusto: échale la crema y las alcaparras tú mismo, no viene mezclado.",
        "practico": "Centro Internacional. Gama media.",
    },
    "Club Colombia": {
        "clave": "La forma más cómoda y elegante de probar lo clásico colombiano bien hecho (grupo Crepes & Waffles).",
        "pedir": "Ajiaco, posta negra y postres criollos — cocina tradicional en versión refinada.",
        "momento": "Bueno para llevar a alguien que quiere probar 'lo típico' sin ir a un sitio popular.",
        "practico": "Zona G, en casa elegante. Gama media-alta.",
    },

    # ════════════════════════ PARRILLA / MARISCOS / INTERNACIONAL ════════════════════════
    "La Cabrera": {
        "clave": "Parrilla de referencia para una comida contundente de carne estilo argentino-colombiano.",
        "pedir": "Cortes a la parrilla; la sede de Zona G es la más concurrida.",
        "momento": "Reserva en hora pico. Buena para grupo carnívoro.",
        "practico": "Varias sedes; Zona G la principal. Gama media-alta.",
    },
    "La Bifería": {
        "clave": "Parrilla del norte con el mejor costo-beneficio en carne de calidad.",
        "pedir": "Los cortes generosos con papa de pellejo y la ensalada de la casa.",
        "momento": "Se llena con familias bogotanas los fines de semana — reserva o ve temprano.",
        "practico": "Norte (Calle 109). Gama media.",
    },
    "Central Cevichería": {
        "clave": "Cevichería animada cerca de la Zona T: el calentamiento perfecto antes de salir.",
        "pedir": "Ceviches y cocteles; ambiente de tarde-noche.",
        "momento": "Tarde-noche, antes de la rumba. Se anima los fines de semana.",
        "practico": "Zona T / El Nogal. Gama media. Informal.",
    },
    "Pesquera Jaramillo": {
        "clave": "Mariscos frescos a 2.600 m de altura — improbable, pero es LA referencia de pescado en Bogotá.",
        "pedir": "Ceviches y cazuelas de mariscos; el frescor del producto es el punto.",
        "momento": "Muy popular — mejor con reserva, sobre todo fin de semana.",
        "ojo": "Es mar a la distancia que es: vienes por el manejo del producto, no por estar junto al mar.",
        "practico": "Usaquén / Santa Bárbara. Gama media-alta.",
    },
    "Pizzardi Artigianale": {
        "clave": "Napolitana certificada (True Neapolitan Pizza Association): de las mejores pizzas del país.",
        "pedir": "Una margherita o napolitana clásica para juzgar la masa fermentada y el horno de leña.",
        "practico": "Zona G. Gama media.",
    },
    "Donostiarra": {
        "clave": "El rincón vasco de Bogotá: dicen que tiene la mejor tortilla española de la ciudad.",
        "pedir": "La tortilla española, pintxos, el pulpo y la merluza, con un vino de Rioja.",
        "momento": "Bueno cuando se te antoja algo ibérico; ambiente de bar español.",
        "ojo": "Es cocina ESPAÑOLA, no local — ve por eso, no por comida colombiana.",
        "practico": "Zona G / Chapinero. Gama media-alta.",
    },
    "La Lucha Sanguchería": {
        "clave": "Almuerzo peruano bueno, rápido y barato en Zona G — apuesta segura sin gastar.",
        "pedir": "Un sánguche peruano contundente; informal y rápido.",
        "practico": "Zona G (varias sedes). Económico.",
    },
    "Crepes & Waffles": {
        "clave": "La cadena más querida de Colombia: apuesta segura en cualquier zona, nunca te 'tumban'.",
        "pedir": "Crepes dulces o salados, ensaladas y los helados. Buen precio y calidad consistente.",
        "dato": "Emplea mayormente a mujeres cabeza de hogar — comprar aquí tiene un trasfondo social que a los locales les gusta.",
        "ojo": "Es crepería/heladería: no esperes platos típicos colombianos aquí, esa no es su carta.",
        "practico": "Varias sedes. Económico-medio.",
    },
    "Crepes & Waffles (Parque 93)": {
        "clave": "La querida cadena colombiana, esta sede junto al Parque de la 93.",
        "pedir": "Crepes dulces y salados, ensaladas y los helados artesanales.",
        "practico": "Parque de la 93. Económico-medio.",
    },
    "Crepes & Waffles (Zona T / Calle 85)": {
        "clave": "La cadena ícono en plena Zona T — comida casual de calidad mientras recorres la zona rosa.",
        "pedir": "Crepes, ensaladas, postres y los helados artesanales.",
        "practico": "Zona T. Económico-medio.",
    },
    "El Corral (Zona T)": {
        "clave": "La hamburguesa colombiana por excelencia (desde 1983): rápida, confiable, un peldaño sobre el fast food.",
        "pedir": "Una hamburguesa a la parrilla — es el clásico nacional.",
        "practico": "Zona T (muchas sedes). Económico.",
    },
    "Andrés Carne de Res (Chía)": {
        "clave": "No es un restaurante, es un universo: hay que vivirlo UNA vez. Carne, show, baile y locura visual.",
        "pedir": "Carne (es su nombre por algo); pero vienes por la EXPERIENCIA tanto como por la comida.",
        "momento": "Viernes o sábado en la noche para el ambiente completo. Reserva.",
        "ojo": "Queda en Chía, ~40 min FUERA de Bogotá: ve y vuelve en app/taxi, no manejes si vas a tomar. La carta es enorme y abrumadora — pide ayuda al mesero.",
        "dato": "Si no quieres salir de la ciudad, su versión urbana es Andrés D.C. en la Zona T (más chica pero el mismo espíritu).",
        "practico": "Chía (afueras). Gama media-alta. Reserva.",
    },

    # ════════════════════════ CAFÉ DE ESPECIALIDAD ════════════════════════
    "Tropicalia Coffee": {
        "clave": "#9 del MUNDO en 2026: la cafetería colombiana mejor posicionada del planeta.",
        "pedir": "Un método (V60, Chemex o Aeropress) para probar un origen colombiano bien hecho — no un café con leche cualquiera.",
        "momento": "Brunch y catas guiadas; puede llenarse fin de semana.",
        "dato": "Si te gusta el café en serio, pide que te recomienden el origen del día y cómo lo preparan.",
        "practico": "Calle 81A #8-23, Chapinero Alto. Gama media.",
    },
    "Azahar Café": {
        "clave": "Especialidad con tostión propia; la sede de Quinta Camacho es de las más bonitas para sentarse.",
        "pedir": "Un filtrado de origen con trazabilidad; bueno para desayunar o trabajar.",
        "momento": "Ideal de mañana para trabajar en la casa antigua.",
        "practico": "Quinta Camacho. Gama media. Buen WiFi.",
    },
    "Café Cultor": {
        "clave": "Tostador de especialidad con causa social: prueba un filtrado distinto cada visita.",
        "pedir": "Pregunta por el origen colombiano del día en filtrado.",
        "practico": "Chapinero. Gama media.",
    },
    "Catación Pública": {
        "clave": "Más que un café: es donde APRENDES a catar café colombiano, no solo a tomarlo.",
        "ver": "La cata o taller guiado — esa es la experiencia, no el café de paso.",
        "momento": "Reserva la experiencia con anticipación; no es para entrar y salir.",
        "practico": "Quinta Camacho. Reserva la cata. Gama media.",
    },
    "Libertario Coffee Roasters": {
        "clave": "Café serio en Chapinero, fuerte en espresso y métodos.",
        "pedir": "Un espresso o un método; tostión propia.",
        "practico": "Chapinero (varias barras). Gama media.",
    },
    "Amor Perfecto": {
        "clave": "Pionera del café de especialidad en Colombia — buena parada cerca del mercado de Usaquén.",
        "pedir": "Un café de origen único; fueron de los primeros en traer esto al consumidor local.",
        "momento": "Combínalo con el mercado de pulgas de Usaquén el domingo.",
        "practico": "Usaquén. Gama media.",
    },
    "Colo Coffee": {
        "clave": "Cafetería amplia con buen WiFi cerca de la 93 — el spot para trabajar o pasar la tarde.",
        "pedir": "Café de especialidad; el plus aquí es el espacio para quedarte.",
        "practico": "Parque de la 93 / Chicó. Gama media. Buen WiFi.",
    },
    "Brot Bakery & Café": {
        "clave": "Panadería-café clásica (1999) famosa por su baguette de chocolate y los desayunos.",
        "pedir": "El baguette de chocolate y un desayuno; terraza agradable.",
        "momento": "Brunch; recurrente en los tops de desayuno de la ciudad.",
        "practico": "Calle 81 #7-93, Zona T / El Nogal. Gama media.",
    },
    "Masa (Rosales)": {
        "clave": "De los mejores desayunos de Bogotá: croissant de almendras, pan recién horneado, pan de canela.",
        "pedir": "Croissant de almendras + pan de canela. Llega caliente del horno.",
        "momento": "Se LLENA los fines de semana — ve temprano o entre semana para no hacer cola.",
        "ojo": "Fin de semana a media mañana puede haber espera larga: madruga si quieres mesa.",
        "practico": "Calle 70A #4-83, Rosales / Zona G. Gama media.",
    },
    "Árbol del Pan": {
        "clave": "Panadería artesanal encantadora — destino de brunch muy querido en Chapinero.",
        "pedir": "Pan artesanal y desayuno; de las panaderías mejor valoradas de la ciudad.",
        "momento": "Brunch; mejor entre semana o temprano el finde.",
        "practico": "Cra 4 #66-46, Chapinero Alto. Gama media.",
    },
    "Juan Valdez Café (Zona G)": {
        "clave": "El símbolo cafetero del país (de la Federación de Cafeteros): café 100% colombiano por todos lados.",
        "pedir": "Un tinto campesino o un cortado para probar el café nacional; ambiente cómodo con WiFi.",
        "dato": "Es la cadena 'oficial' del café colombiano — buena para un café decente garantizado, aunque no es el especialidad de barra de autor.",
        "practico": "Zona G (muchas sedes). Económico-medio.",
    },

    # ════════════════════════ VIDA NOCTURNA / BARES ════════════════════════
    "Andrés D.C.": {
        "clave": "La versión urbana del mítico Andrés: cuatro pisos de fiesta para EMPEZAR la noche en la Zona Rosa.",
        "pedir": "Comida + tragos mientras subes de piso; cada planta tiene su ambiente.",
        "momento": "Plan de inicio de noche. Reserva fin de semana.",
        "ojo": "Es seguro y emblemático pero turístico y no barato. La experiencia original de verdad es Andrés en Chía.",
        "practico": "Zona T / Zona Rosa. Gama media-alta. Ir en app.",
    },
    "Gaira Café Cumbia House": {
        "clave": "El bar de la familia Vives: vallenato, cumbia y comida del Caribe en vivo. Plan colombiano y festivo.",
        "pedir": "Comida caribeña + la música en vivo; vienes a bailar y celebrar.",
        "momento": "Reserva fin de semana; la música en vivo es el plan.",
        "practico": "Chicó / Calle 96. Gama media-alta.",
    },
    "Theatron": {
        "clave": "La disco LGBTIQ+ más grande de Latinoamérica: muchas salas, cada una su género, en un teatro viejo.",
        "ver": "Recorre las salas — cada una es un mundo musical distinto bajo el mismo techo.",
        "momento": "Fin de semana, abre tarde. Ve en GRUPO y llega/sal en app o taxi.",
        "ojo": "La entrada suele incluir barra (trago libre): mídete, porque pega. Espacio enorme — acuerden punto de encuentro.",
        "practico": "Chapinero. Entrada con barra incluida. Ir en app.",
    },
    "Armando Records": {
        "clave": "El rooftop indie de la Calle 85: música alternativa y terraza, referencia de la rumba joven.",
        "pedir": "Tragos en la terraza; el ambiente es el producto.",
        "momento": "Noche, fin de semana. La terraza es el punto.",
        "practico": "Zona T. Gama media. Ir en app.",
    },
    "Baum": {
        "clave": "El club de electrónica serio de Bogotá (techno/house) con line-ups internacionales.",
        "momento": "Abre TARDE — no llegues a medianoche esperando que esté lleno, esto arranca de madrugada.",
        "ojo": "Es para rumba electrónica de verdad: si buscas crossover/reggaetón, no es aquí. Ir y volver en app.",
        "practico": "Centro / Las Nieves. Ir en app, zona a cuidar de noche.",
    },
    "Video Club": {
        "clave": "Electrónica referente de la ciudad: techno, house y DJs internacionales en tres ambientes.",
        "momento": "Jue–Sáb desde las 10pm; arranca tarde.",
        "ojo": "Dos pistas + terraza: la escena electrónica más seria de Bogotá, no un bar de copas tranquilo.",
        "practico": "Chapinero. Ir en app.",
    },
    "Apache Rooftop (Click Clack)": {
        "clave": "Azotea del hotel Click Clack: cocteles al atardecer con vista a los cerros. El plan fotogénico y relajado.",
        "pedir": "Un coctel al atardecer — la hora dorada con los cerros de fondo es el momento.",
        "momento": "Atardecer. Más chill que una discoteca.",
        "practico": "Zona G, cerca del Parque 93. Gama media-alta.",
    },
    "Galería Café Libro": {
        "clave": "Templo de la salsa: orquesta en vivo y baile hasta tarde. Si quieres salsa de verdad, es aquí.",
        "ver": "La pista con orquesta en vivo — vienes a bailar, no a sentarte.",
        "momento": "Fin de semana para la orquesta. Hay sede en Zona T y en el centro.",
        "ojo": "Es salsa brava: si no bailas, igual disfrutas, pero el plan es la pista.",
        "practico": "Zona T / Centro. Gama media.",
    },
    "Bogotá Beer Company (BBC)": {
        "clave": "La cervecería artesanal más conocida: plan informal y seguro para arrancar antes de la rumba.",
        "pedir": "Una cerveza artesanal de la casa; ambiente de pub.",
        "dato": "Tiene sedes por toda la ciudad — siempre hay una cerca de donde estés.",
        "practico": "Varias sedes. Económico-medio.",
    },
    "Taninos Park Wines": {
        "clave": "Bar de vinos en el Parkway (+140 etiquetas): plan tranquilo de copas en zona bohemia y caminable.",
        "pedir": "Una copa de la carta amplia; panorama de noche relajada, no de rumba.",
        "momento": "Noche tranquila. El Parkway es bonito para caminar antes/después.",
        "practico": "Parkway / La Soledad. Gama media.",
    },
    "Quiebracanto": {
        "clave": "Salsa clásica con +40 años de historia, fundado por estudiantes de la Nacional. La salsa auténtica.",
        "ver": "Las orquestas en vivo de los viernes — ese es el plan.",
        "momento": "Mié–Jue 6pm–3am, Vie–Sáb 4pm–3am. Viernes para la orquesta.",
        "practico": "La Candelaria / Centro. Económico-medio. Ir en app de noche.",
    },
    "Son Havana": {
        "clave": "Salsa clásica y son cubano de vieja guardia, intimista y auténtico. Favorito universitario.",
        "ver": "Las clases de baile (mié, jue y sáb 7–9pm) si quieres soltarte antes de la pista.",
        "momento": "Jue–Sáb hasta las 5am.",
        "practico": "Chapinero. Económico-medio. Ir en app.",
    },

    # ════════════════════════ MERCADOS ════════════════════════
    "Mercado de pulgas de Usaquén": {
        "clave": "Plan de domingo: antigüedades y artesanías en el casco colonial, combinado con brunch en la plaza.",
        "ver": "Los puestos de antigüedades y artesanías; el ambiente del pueblito dentro de la ciudad.",
        "momento": "DOMINGOS (es cuando se arma de verdad). Mañana para el mercado, mediodía para el brunch.",
        "ojo": "Entre semana no está el mercado — si vas un martes, te encuentras solo la plaza. Es plan dominical.",
        "practico": "Usaquén. Gratis entrar. Lleva algo de efectivo para los puestos.",
    },
    "Plaza de Mercado de Paloquemao": {
        "clave": "La plaza más famosa de Bogotá: frutas exóticas, flores y desayuno entre puestos. Imperdible.",
        "ver": "El pasillo de frutas exóticas (pídeles que te las hagan probar) y, temprano, el mercado de flores.",
        "momento": "TEMPRANO, antes de las 9am — las flores y lo mejor del producto se ven a primera hora.",
        "ojo": "Es una plaza de mercado real y caótica, no un sitio turístico pulido. Cuida tus cosas y lleva efectivo.",
        "dato": "Hay tours de frutas con guía — la forma más fácil de probar 10 frutas que no conoces sin perderte.",
        "practico": "Paloquemao. Efectivo. Ir temprano y de día.",
    },
    "Plaza de Mercado La Perseverancia": {
        "clave": "El secreto local: almuerzos caseros buenísimos y baratos entre puestos. Lo más auténtico.",
        "pedir": "Sancocho, lechona o tamal en uno de los puestos de almuerzo. Comida de verdad, casera.",
        "momento": "AL MEDIODÍA — es plaza de almuerzo. Fuera de esa hora pierde la gracia.",
        "ojo": "Barrio sencillo: ve de día, mediodía, con efectivo y sin alardear. El plan es comer rico y local, no turistear.",
        "practico": "La Perseverancia / Centro. Económico. Efectivo.",
    },

    # ════════════════════════ MIRADORES ════════════════════════
    "Cerro de Monserrate": {
        "clave": "El mirador insignia (3.152 m): se sube en funicular o teleférico, NO caminando de noche.",
        "ver": "La vista de toda la sabana desde arriba y el santuario; al atardecer es lo máximo.",
        "momento": "Atardecer ENTRE SEMANA = menos fila y mejor luz. Fin de semana se llena.",
        "ojo": "Estás a 3.152 m: el aire es más delgado, ve con calma. El sendero a pie solo de día y acompañado — de noche, funicular/teleférico siempre.",
        "dato": "Combínalo con la Quinta de Bolívar, que queda a sus pies. Lleva algo de abrigo: arriba hace más frío y viento.",
        "practico": "La Candelaria / Cerros. Boleto de funicular/teleférico. Plan del día 1.",
    },
    "Mirador Torre Colpatria": {
        "clave": "Vista de 360° desde 196 m en el primer rascacielos de la ciudad — pero abre poquísimo.",
        "ver": "El panorama de 360° de Bogotá desde el centro.",
        "momento": "SOLO viernes, sábado, domingo y festivos. CONFIRMA el horario antes de ir o pierdes el viaje.",
        "ojo": "Entre semana está cerrado al público — el error más común es llegar un martes y encontrarlo cerrado.",
        "practico": "Centro Internacional. Económico. Confirmar horario.",
    },

    # ════════════════════════ ATRACCIONES / MUSEOS ════════════════════════
    "Museo del Oro": {
        "clave": "Imprescindible: una de las mejores colecciones de orfebrería prehispánica del mundo. Gratis los domingos.",
        "ver": "La Sala de la Ofrenda (la oscura del final) — guárdala para el cierre, es el clímax del recorrido.",
        "momento": "GRATIS los domingos (por eso, más lleno ese día). Dedícale 1.5–2 horas. Cierra lunes.",
        "ojo": "Cierra los LUNES — el tropiezo clásico. Si solo tienes el lunes, planea otra cosa.",
        "dato": "Está a 10 min a pie del Museo Botero (gratis): se hacen los dos en una mañana de centro.",
        "practico": "La Candelaria. Entrada económica, gratis domingos. ~2h.",
    },
    "Museo Botero": {
        "clave": "Entrada GRATUITA y no solo es Botero: hay Picasso, Dalí, Monet y Renoir donados por él.",
        "ver": "La colección internacional (Picasso, Dalí, Monet) además de las obras de Botero — sorprende a todos.",
        "momento": "A 10 min a pie del Museo del Oro: hazlos juntos. Cierra martes (confirmar).",
        "dato": "Es gratis siempre, en casa colonial — uno de los mejores planes gratis del centro.",
        "practico": "La Candelaria. GRATIS. ~1–1.5h.",
    },
    "Museo Nacional de Colombia": {
        "clave": "El museo más antiguo del país, dentro de una antigua penitenciaría (El Panóptico).",
        "ver": "El propio edificio carcelario (El Panóptico) es parte del atractivo, además de historia y arte.",
        "momento": "Gratis los domingos. Plan de medio día.",
        "practico": "Centro Internacional. Económico, gratis domingos.",
    },
    "MAMBO — Museo de Arte Moderno": {
        "clave": "Arte moderno y contemporáneo latinoamericano en edificio de Rogelio Salmona.",
        "ver": "Las exposiciones rotativas — revisa qué hay montado antes de ir, cambian seguido.",
        "practico": "Centro Internacional. Económico.",
    },
    "Quinta de Bolívar": {
        "clave": "La casa donde vivió Bolívar, con jardines al pie de Monserrate. Tranquila y bonita.",
        "ver": "La casa-museo y sus jardines; combínala con la subida a Monserrate (quedan juntos).",
        "practico": "La Candelaria / Cerros. Económico.",
    },
    "Biblioteca Luis Ángel Arango": {
        "clave": "De las bibliotecas más visitadas del mundo: arte, conciertos de cámara GRATIS y la mejor sala de lectura.",
        "ver": "Las salas de exposición y, si coincide, un concierto de cámara gratuito.",
        "momento": "A pasos del Museo del Oro y el Teatro Colón — encadénalos.",
        "dato": "Entrada libre a buena parte de sus espacios; refugio perfecto si llueve en el centro.",
        "practico": "La Candelaria. GRATIS.",
    },
    "Teatro Colón": {
        "clave": "El teatro nacional (1892): techos pintados a mano y acústica excepcional. Joya del centro.",
        "ver": "El tour guiado de los interiores restaurados, o mejor, una función.",
        "momento": "Visitas guiadas diarias 10am–4pm (español, inglés, portugués). Consulta cartelera para funciones.",
        "practico": "La Candelaria. Tour económico.",
    },
    "Maloka": {
        "clave": "Centro interactivo de ciencia con cine domo: el plan salvavidas con niños o si llueve.",
        "ver": "El cine domo y las salas interactivas.",
        "momento": "Ideal en familia, y perfecto cuando el clima no acompaña.",
        "practico": "Salitre. Económico-medio. Plan en familia.",
    },
    "Plaza de Bolívar": {
        "clave": "El corazón histórico: punto de partida natural para recorrer La Candelaria.",
        "ver": "La Catedral Primada, el Capitolio y el Palacio de Justicia alrededor; las palomas y el ambiente.",
        "momento": "De día. Es el inicio lógico antes del Museo del Oro y el Botero.",
        "ojo": "De noche el centro se vacía: recorre la Candelaria de día y, al caer la tarde, muévete en app.",
        "practico": "La Candelaria. Gratis. Punto de partida del centro.",
    },
    "Chorro de Quevedo": {
        "clave": "La plazoleta donde la leyenda funda Bogotá: bohemia, cuenteros, chicha y arte urbano.",
        "ver": "El arte urbano de los callejones alrededor y, si hay, los cuenteros. Pruébate una chicha.",
        "momento": "De DÍA es seguro y encantador. De noche, ir acompañado.",
        "ojo": "Es punto de salida del graffiti tour. De noche cambia el ambiente — no andes solo mostrando el celular.",
        "practico": "La Candelaria. Gratis.",
    },
    "Media Torta": {
        "clave": "Anfiteatro al aire libre al pie de los cerros: conciertos GRATIS de música colombiana y jazz.",
        "ver": "Un concierto gratuito si coincide con tu visita — revisa la cartelera.",
        "momento": "Consulta cartelera en cultura.gov.co antes de ir; los eventos son por fechas.",
        "practico": "La Candelaria / Cerros. Gratis (según evento).",
    },
    "Cinemateca de Bogotá": {
        "clave": "Cine de autor y patrimonio: filmes que no llegan a salas comerciales, económico o gratis.",
        "ver": "Un ciclo temático o una retrospectiva — revisa la programación.",
        "practico": "Centro Internacional. Económico/gratis.",
    },
    "Museo de Arte Moderno de Bogotá (MAMBO)": {
        "clave": "La mejor colección de arte colombiano del siglo XX: Obregón, Botero, Negret.",
        "ver": "La colección del XX colombiano y las temporales contemporáneas; está junto al Planetario.",
        "practico": "Teusaquillo / Centro. Económico.",
    },

    # ════════════════════════ PARQUES ════════════════════════
    "Parque El Virrey": {
        "clave": "Corredor verde para caminar o trotar en el norte, rodeado de cafés. Muy seguro de día.",
        "momento": "De día. Bueno para una caminata entre comidas en el norte.",
        "practico": "El Nogal / Chicó. Gratis.",
    },
    "Parque Nacional Enrique Olaya Herrera": {
        "clave": "El parque urbano histórico entre el centro y los cerros — verde y cerca de La Macarena.",
        "momento": "Bonito de día; combínalo con los restaurantes de La Macarena.",
        "practico": "La Macarena / Centro. Gratis. De día.",
    },
    "Jardín Botánico José Celestino Mutis": {
        "clave": "Lago, palmetum y plantas de páramo: plan tranquilo de medio día con buen clima.",
        "ver": "El bosque andino y las plantas de páramo — flora que no ves en otra ciudad.",
        "momento": "Con buen clima (revisa el cielo bogotano antes). Medio día.",
        "practico": "Salitre. Económico.",
    },
    "Parque Metropolitano Simón Bolívar": {
        "clave": "El gran pulmón verde de Bogotá: lago, ciclorrutas y sede de los grandes conciertos.",
        "ver": "El lago y las zonas verdes para picnic; aquí pasan los festivales grandes.",
        "practico": "Salitre. Gratis. De día.",
    },
    "Parque de la 93": {
        "clave": "Parque rodeado de terrazas y restaurantes — plan tranquilo al aire libre en zona muy segura.",
        "momento": "Tarde para fotos o un café; eventos los fines de semana.",
        "practico": "Chicó. Gratis. De las zonas más seguras del norte.",
    },

    # ════════════════════════ EXPERIENCIAS ════════════════════════
    "Tour de grafiti por La Candelaria": {
        "clave": "Bogotá es referente mundial de grafiti: este es el mejor intro al centro y su historia reciente.",
        "ver": "Los murales grandes con su contexto político — el guía explica lo que no ves solo.",
        "momento": "Tours por propinas salen a diario, de día. Llega 10 min antes.",
        "ojo": "Es 'gratis' pero por PROPINA — lleva efectivo para dejar lo justo al guía al final.",
        "practico": "La Candelaria. Propina sugerida. Lleva efectivo.",
    },
    "Quebrada La Vieja (senderismo)": {
        "clave": "El plan local más querido para arrancar el día: caminata por los cerros DENTRO de la ciudad.",
        "ver": "El bosque, el agua y las vistas de la ciudad despertando.",
        "momento": "TEMPRANO — abre al amanecer y cierra ~10–11am. Si llegas a mediodía, ya cerró.",
        "ojo": "Suele requerir registro y tiene horario de cierre estricto. Gratis, pero madruga.",
        "practico": "Chapinero Alto / Cerros. Gratis. Solo mañana.",
    },
    "Ciclovía dominical": {
        "clave": "Lo más bogotano: domingos 7am–2pm cierran +120 km de vías para bicis y peatones.",
        "ver": "Recorre la Carrera 7 o la Calle 26 en bici, con la ciudad sin carros.",
        "momento": "Domingos y festivos, 7am–2pm. Ve por la mañana antes de que cierre.",
        "dato": "Alquila una bici cerca de tu zona; es gratis y es la mejor forma de sentir la ciudad como local.",
        "practico": "Toda la ciudad. Gratis. Alquiler de bici aparte.",
    },
    "Tour en bici por Bogotá": {
        "clave": "La mejor forma de entender la ciudad en medio día: centro, plazas de mercado y grafiti en bici.",
        "ver": "Conecta el centro, una plaza de mercado y los murales — todo de un tirón.",
        "momento": "Operadores reconocidos salen de La Candelaria, de día.",
        "practico": "La Candelaria. Medio día. Reserva con operador.",
    },
    "Graffiti Tour La Candelaria": {
        "clave": "Tour gratuito (propina) reconocido entre los mejores del mundo: arte urbano con contexto político.",
        "ver": "Los murales clave con la historia detrás; guías que explican el trasfondo social.",
        "momento": "Sale de la Plaza del Chorro de Quevedo. Reserva en bogotaGraffitiTour.com.",
        "ojo": "Gratis pero por propina — lleva efectivo para el guía.",
        "practico": "La Candelaria. Propina sugerida. Reservar online.",
    },

    # ════════════════════════ EXCURSIONES (día completo desde Bogotá) ════════════════════════
    "Catedral de Sal de Zipaquirá": {
        "clave": "La excursión clásica: una catedral excavada DENTRO de una mina de sal, a ~1.5h al norte.",
        "ver": "Las naves y la cruz iluminada bajo tierra; combínala con el pueblo de Zipaquirá.",
        "momento": "Día COMPLETO. Hay tren turístico los fines de semana (lindo plan en sí mismo).",
        "ojo": "Es bajo tierra y fresco: lleva abrigo. No la metas como medio día — el viaje ida y vuelta ya se come horas.",
        "practico": "Zipaquirá (afueras). Día completo. Tour o tren turístico fin de semana.",
    },
    "Laguna de Guatavita": {
        "clave": "La laguna sagrada muisca, origen de la leyenda de El Dorado, a ~1.5–2h.",
        "ver": "El recorrido guiado por la reserva con vistas a la laguna; combínala con el pueblo de Guatavita.",
        "momento": "Día completo. El acceso a la laguna es con recorrido guiado.",
        "ojo": "No se puede bajar a tocar el agua (es reserva sagrada protegida) — vas por la historia y el paisaje.",
        "practico": "Guatavita / Sesquilé (afueras). Día completo.",
    },
    "Mina de Sal de Nemocón": {
        "clave": "La alternativa íntima a Zipaquirá: túneles de sal con espejos de agua espectaculares.",
        "ver": "Los espejos de agua subterráneos (allí se grabó parte de la película '33').",
        "momento": "Día completo al norte. Menos turística y más tranquila que Zipaquirá.",
        "dato": "Si te dijeron 've a Zipaquirá' pero quieres algo menos masificado, esta es la jugada local.",
        "practico": "Nemocón (afueras). Día completo.",
    },
    "Villa de Leyva": {
        "clave": "Pueblo colonial con una de las plazas empedradas más grandes de América — pero es de QUEDARSE, no de ida y vuelta.",
        "ver": "La plaza enorme, los viñedos, los fósiles y el desierto de la Candelaria.",
        "momento": "Plan de FIN DE SEMANA con noche, no de día: está a ~3.5–4h.",
        "ojo": "El error es intentarlo en un día: son 7–8h de carro ida y vuelta. Reserva alojamiento y quédate una noche.",
        "practico": "Boyacá (afueras). Mínimo una noche.",
    },
    "Parque Natural Chicaque": {
        "clave": "Bosque de niebla a ~1h: naturaleza de verdad y cabañas en los árboles sin alejarse.",
        "ver": "Los senderos entre la niebla y el aire de páramo; cabañas en los árboles.",
        "momento": "Día de caminata. Lleva calzado de senderismo e impermeable.",
        "practico": "San Antonio del Tequendama (afueras). Día completo.",
    },
    "Suesca (senderismo y escalada)": {
        "clave": "La meca de la escalada en roca cerca de Bogotá (~1.5h), con rutas hasta para principiantes.",
        "ver": "Las paredes a la orilla del río; también se puede solo caminar si no escalas.",
        "momento": "Plan de aventura de un día. Hay guías para principiantes.",
        "practico": "Suesca (afueras). Día completo. Guía si vas a escalar.",
    },

    # ════════════ SEGUNDA TANDA — resto del núcleo experiencial de Bogotá ════════════
    # RESTAURANTES
    "Selma": {
        "clave": "El segundo proyecto de Álvaro Clavijo (El Chato): más accesible que El Chato, misma mano.",
        "pedir": "Déjate llevar por la carta de temporada; cocina de autor en casa restaurada de Chapinero.",
        "momento": "Reserva. Buena alternativa si El Chato no tiene mesa.",
        "practico": "Chapinero. Gama media-alta.",
    },
    "Debora": {
        "clave": "Una de las cocinas jóvenes más interesantes (#85 ext.): hiperestacionalidad y producto colombiano preciso.",
        "pedir": "El menú de temporada del chef Jacobo Bonilla; platos coloridos y precisos.",
        "momento": "Reserva recomendada.",
        "practico": "Gama media-alta.",
    },
    "Villanos en Bermudas": {
        "clave": "Cocina de autor atrevida y de culto entre foodies: sin etiquetas, mucha técnica.",
        "pedir": "Confía en la carta corta y cambiante; es para una cena de exploración, no de clásicos.",
        "momento": "Reserva. Ambiente joven, ideal de noche.",
        "ojo": "No es comida típica ni segura: vienes a que te sorprendan. Si quieres algo predecible, no es aquí.",
        "practico": "Gama media-alta.",
    },
    "Cuzco": {
        "clave": "Alta cocina peruana en el Parque 93: ceviches y tiraditos en ambiente elegante.",
        "pedir": "Ceviches y tiraditos con producto fresco; bueno para almuerzo de negocios o cena.",
        "momento": "Reserva en hora pico.",
        "practico": "Parque de la 93. Gama media-alta.",
    },
    "Osaka": {
        "clave": "Nikkei (japonés-peruano) de la cadena latinoamericana de culto: tiraditos y makis sofisticados.",
        "pedir": "Los tiraditos nikkei y los makis; platos de wok. Ambiente para los amantes del sushi.",
        "momento": "Reserva fin de semana.",
        "practico": "Gama alta.",
    },
    "Gordo": {
        "clave": "Restaurante de barrio de culto local: brasa, vinos naturales y carta corta que cambia.",
        "pedir": "Lo que esté a la brasa ese día + un vino natural. Carta corta, relajada.",
        "momento": "Favorito de la escena local; reserva si vas en grupo.",
        "practico": "Chapinero Alto. Gama media.",
    },
    "Misia": {
        "clave": "La cara CASUAL de Leonor Espinosa (Leo): cocina popular colombiana sabrosa y alegre, sin el precio de Leo.",
        "pedir": "Cazuelas, fritos y sabores del Pacífico y el Caribe — comida popular bien hecha.",
        "momento": "Bueno para almuerzo o cena relajada. Buena entrada al universo de Leonor sin gastar como en Leo.",
        "dato": "Si quieres probar la cocina de Leonor Espinosa pero Leo es mucho presupuesto, este es el atajo.",
        "practico": "Gama media.",
    },
    "Mistral": {
        "clave": "Brasserie mediterránea en casa de Quinta Camacho: fuerte en desayunos y planes de día.",
        "pedir": "Desayuno o almuerzo con producto fresco y la panadería propia.",
        "momento": "Mejor de día. Bonito para brunch.",
        "practico": "Quinta Camacho. Gama media.",
    },
    "Di Lucca": {
        "clave": "Italiano clásico y confiable, el favorito de los bogotanos para reunión familiar.",
        "pedir": "Pastas, risottos y pizzas en porciones generosas; ambiente cálido de trattoria.",
        "momento": "Bueno en grupo/familia. Reserva fin de semana.",
        "practico": "Gama media.",
    },
    "Watakushi": {
        "clave": "Sushi y asiático contemporáneo con coctelería en la Zona T: cena animada antes de salir.",
        "pedir": "Sushi y platos asiáticos con un coctel; ambiente joven y de negocios.",
        "momento": "Tarde-noche, antes de la rumba de la Zona T.",
        "practico": "Zona T. Gama media-alta.",
    },
    "La Cervecería Libre": {
        "clave": "Cervecería artesanal de barrio en Chapinero: tarde-noche informal con cervezas locales.",
        "pedir": "Una artesanal local con algo de la cocina de acompañamiento.",
        "practico": "Chapinero. Económico-medio.",
    },
    "Capital Cocina": {
        "clave": "Cocina colombiana contemporánea en casa de La Candelaria: el premio confiable tras los museos.",
        "pedir": "Platos colombianos contemporáneos; opción segura y sabrosa en pleno centro.",
        "momento": "Almuerzo, después del Museo del Oro / Botero.",
        "practico": "La Candelaria. Gama media.",
    },
    "Sorella": {
        "clave": "Italiano de Chapinero con pasta fresca hecha en casa: acogedor y muy recomendado.",
        "pedir": "Los agnolotti de provolone con hongos y las pizzas de masa cuidada.",
        "momento": "Bueno para cena. Reserva fin de semana.",
        "practico": "Chapinero. Gama media.",
    },
    "Fiero": {
        "clave": "Parrilla uruguaya referente en Zona G: cortes a la brasa de gran calidad para carnívoros.",
        "pedir": "Cortes a la brasa; el punto y el servicio son su fuerte.",
        "momento": "Reserva en hora pico.",
        "practico": "Zona G. Gama media-alta.",
    },
    "Insurgentes": {
        "clave": "De los mejores mexicanos casuales de la ciudad: tacos con sabor de verdad y cervezas con amigos.",
        "pedir": "Tacos auténticos; quédate a tomar cervezas, el ambiente invita.",
        "momento": "Casual, rápido. Bueno de tarde-noche.",
        "practico": "Económico-medio.",
    },
    "Cecilia": {
        "clave": "Italiano de Usaquén para citas y celebraciones: pasta, pizza y entradas notables.",
        "pedir": "Pasta y pizza con entradas; ambiente cálido y servicio cuidado.",
        "momento": "Cena, citas. Reserva.",
        "practico": "Usaquén. Gama media-alta.",
    },
    "Armadillo": {
        "clave": "Parrilla elegante y cálida: el clásico de carnes para una cena romántica.",
        "pedir": "Carnes a la brasa con buen punto.",
        "momento": "Cena romántica. Reserva.",
        "practico": "Gama media-alta.",
    },
    "Wok": {
        "clave": "Cadena local asiática con foco en pesca responsable: fresca, confiable y de buen precio.",
        "pedir": "Platos tailandeses, japoneses o vietnamitas; producto sostenible.",
        "dato": "Varias sedes — apuesta segura de asiático sin gastar mucho.",
        "practico": "Varias sedes. Económico-medio.",
    },
    "Quinua y Amaranto": {
        "clave": "Vegetariano clásico de La Candelaria: menú del día casero, sano y económico.",
        "pedir": "El menú del día vegetariano; acogedor y barato.",
        "momento": "Almuerzo, tras los museos del centro.",
        "practico": "La Candelaria. Económico.",
    },
    "Pajares Salinas": {
        "clave": "Institución española desde 1956: paellas, cochinillo y mariscos para una comida formal.",
        "pedir": "Paella, cochinillo o mariscos; sabores ibéricos clásicos.",
        "ojo": "Es cocina ESPAÑOLA formal, no local — ve por eso. Ambiente clásico, no casual.",
        "practico": "Gama media-alta.",
    },
    "Abasto": {
        "clave": "El brunch de fin de semana más querido: cocina de mercado con producto del campo colombiano.",
        "pedir": "Los huevos campesinos y la panadería propia; desayunos célebres.",
        "momento": "Brunch de FIN DE SEMANA — se llena, ve temprano.",
        "ojo": "Fin de semana a media mañana hay espera: madruga o ve entre semana.",
        "practico": "Gama media.",
    },

    # CAFÉS
    "Varietale": {
        "clave": "Punto de encuentro de la comunidad cafetera: aquí corren competencias como la Barista League.",
        "pedir": "Un método de especialidad de altísimo nivel; ambiente para aficionados al café.",
        "practico": "Gama media.",
    },
    "Devoción": {
        "clave": "Tuesta de finca a taza (también tiene sede en Nueva York): café colombiano trazable en espacio de diseño.",
        "pedir": "Un café de origen trazable; espacio cálido ideal para trabajar.",
        "practico": "Gama media. Buen WiFi.",
    },
    "Café San Alberto (Candelaria)": {
        "clave": "El café más premiado de Colombia: aquí se viene a CATAR, con guía, no solo a tomar.",
        "ver": "La cata guiada — es la mejor forma de entender el café colombiano premium.",
        "momento": "Reserva la cata. En pleno centro histórico, a pasos de los museos.",
        "practico": "La Candelaria. Cata premium.",
    },
    "Arte y Pasión Café": {
        "clave": "Campeones nacionales de barismo y latte art: para ver el oficio del barista en su mejor nivel.",
        "pedir": "Un espresso o un latte para apreciar la técnica; hay talleres.",
        "practico": "Gama media.",
    },
    "Café Quindío Premium": {
        "clave": "Tienda-café del Eje Cafetero: buena para LLEVAR café a casa y probar clásicos.",
        "pedir": "Bebidas clásicas + compra café de origen para llevar.",
        "dato": "Más tienda para souvenirs de café de calidad que barra de especialidad de autor.",
        "practico": "Económico-medio.",
    },
    "Masa (Quinta Camacho)": {
        "clave": "La otra sede de Masa: pan de masa madre y desayunos generosos de culto.",
        "pedir": "Pan de masa madre, pastelería y un desayuno generoso.",
        "momento": "Filas los fines de semana por algo — ve temprano o entre semana.",
        "practico": "Quinta Camacho. Gama media.",
    },

    # BARES
    "La Sala de Laura": {
        "clave": "EL mejor bar de Bogotá (#68 World's 50 Best Bars 2025): coctelería de autor que exalta lo colombiano. Cada piso, una experiencia.",
        "pedir": "Déjate guiar por la coctelería de autor; sube de piso, cada uno cambia la vibra.",
        "momento": "Reserva — es de los bares más pedidos de la ciudad. Noche.",
        "dato": "Si solo vas a tomarte UN coctel bueno en Bogotá, que sea aquí.",
        "practico": "Gama alta. Reserva. Ir en app.",
    },
    "Astoria Rooftop": {
        "clave": "Rooftop sofisticado en el AC by Marriott (Zona T), 15 pisos arriba: cocteles con vista a los cerros.",
        "pedir": "Un coctel de autor al atardecer con la vista de los cerros.",
        "momento": "Atardecer. Ambiente tipo lounge.",
        "practico": "Zona T. Gama media-alta.",
    },
    "Federal Rooftop": {
        "clave": "Rooftop FESTIVO de la Zona Rosa: DJ desde las 9pm y brunch los sábados. Tragos con fiesta.",
        "pedir": "El martini de maracuyá o un mojito helado; ambiente de fiesta.",
        "momento": "DJ desde las 9pm; brunch sábados. Para mezclar copas con rumba.",
        "practico": "Zona Rosa. Gama media-alta. Ir en app.",
    },
    "Sky 15 Rooftop": {
        "clave": "En el techo del Hilton, 54 m sobre Chapinero: vista de 360° al skyline, exclusivo pero abierto al público.",
        "pedir": "Cocteles con la parrilla y la vista de 360°.",
        "momento": "Atardecer/noche. A veces con DJ.",
        "practico": "Chapinero. Gama alta.",
    },
    "El Mono Bandido": {
        "clave": "Cervecería tropical y relajada: amplia carta de artesanales, sidra y comida casera.",
        "pedir": "Una artesanal de la carta (Lager, Ale) o sidra; comida casera de acompañamiento.",
        "practico": "Económico-medio. Ambiente relajado.",
    },
    "Bogotá Beer Company (Zona T)": {
        "clave": "La BBC en la Zona T: buen after-office con cervezas propias antes de salir.",
        "pedir": "Una artesanal propia de la casa; ambiente animado de after-office.",
        "practico": "Zona T. Económico-medio.",
    },
    "Smoking Molly": {
        "clave": "Bar de rock sin pretensiones en Chapinero: buena música, hamburguesas y plan relajado.",
        "pedir": "Cervezas + una hamburguesa, con rock de fondo. Plan desenfadado.",
        "practico": "Chapinero. Económico-medio.",
    },

    # ATRACCIONES
    "Catedral Primada de Colombia": {
        "clave": "La catedral principal del país, sobre la Plaza de Bolívar: parada obligada al recorrer el centro.",
        "ver": "La arquitectura neoclásica imponente; entra al recorrer la Plaza de Bolívar.",
        "momento": "De día, junto con la Plaza de Bolívar y los museos.",
        "practico": "La Candelaria. Gratis/económico.",
    },
    "Iglesia de San Francisco": {
        "clave": "La iglesia más antigua de Bogotá (s. XVII): su altar dorado barroco es de los más bellos del país.",
        "ver": "El altar mayor dorado — una joya escondida en pleno centro, junto al Museo del Oro.",
        "momento": "Combínala con el Museo del Oro, queda al lado.",
        "practico": "La Candelaria. Gratis.",
    },
    "Centro Cultural Gabriel García Márquez": {
        "clave": "Edificio de Rogelio Salmona en ladrillo: librería del FCE, exposiciones y vista al centro.",
        "ver": "La arquitectura en ladrillo y la terraza con vista; buen alto cultural en La Candelaria.",
        "practico": "La Candelaria. Gratis entrar.",
    },
    "Museo de Bogotá": {
        "clave": "Cuenta la historia de la ciudad: ideal para ENTENDER Bogotá ANTES de recorrerla.",
        "ver": "Las exposiciones sobre la transformación de la ciudad, en casa colonial.",
        "momento": "Buen primer plan del viaje para contextualizar todo lo demás.",
        "practico": "La Candelaria. Económico.",
    },
    "Planetario de Bogotá": {
        "clave": "Domo astronómico y museo del espacio junto al Parque de la Independencia: plan familiar.",
        "ver": "La proyección del domo y el museo del espacio.",
        "momento": "Bueno en familia o si llueve.",
        "practico": "Centro. Económico.",
    },
    "Casa de Nariño": {
        "clave": "El palacio presidencial: se ve el cambio de guardia, y con reserva previa hay recorridos.",
        "ver": "El cambio de guardia presidencial; recorrido interior solo CON reserva anticipada.",
        "momento": "El recorrido requiere reserva previa (con documento) — no se entra sin agendar.",
        "ojo": "No es de entrar a la pasada: el tour interior se gestiona con anticipación. Sin reserva, solo ves el exterior y la guardia.",
        "practico": "La Candelaria. Gratis con reserva.",
    },
    "Cementerio Central": {
        "clave": "Monumento nacional con mausoleos históricos y arte funerario: un plan cultural distinto.",
        "ver": "Los mausoleos históricos y el arte funerario; cerca queda La Macarena.",
        "momento": "De día. Combínalo con un almuerzo en La Macarena.",
        "practico": "Centro. Gratis.",
    },
    "Barrio La Macarena": {
        "clave": "El barrio bohemio a los pies de los cerros: galerías, restaurantes de autor y cafés. Para caminar y comer distinto.",
        "ver": "Las galerías de arte y la oferta de restaurantes de autor; ambiente artístico.",
        "momento": "De día para galerías; tarde-noche para comer. Caminable.",
        "dato": "Es el barrio para quien quiere arte contemporáneo y comida de autor sin la formalidad del norte.",
        "practico": "La Macarena / Centro. Caminable de día.",
    },
    "Usaquén (centro histórico)": {
        "clave": "Pueblo colonial dentro de la ciudad: plaza, iglesia, calles adoquinadas y el mercado de pulgas dominical.",
        "ver": "La plaza colonial, las calles adoquinadas y, si es domingo, el mercado de pulgas.",
        "momento": "DOMINGO es el mejor día (mercado de pulgas + brunch). Plan de día completo en el norte.",
        "practico": "Usaquén. Gratis. Muy seguro.",
    },
    "Monumento a los Héroes / Zona": {
        "clave": "Más un punto de orientación y nodo de transporte que una visita en sí.",
        "dato": "Úsalo para ubicarte, no como parada turística — es un hito urbano, no un atractivo.",
        "practico": "Norte. Nodo de transporte.",
    },

    # PARQUES
    "Parque de los Novios": {
        "clave": "Parque con lago donde se alquilan botes: respiro verde popular en familia los fines de semana.",
        "ver": "El lago con botes y las zonas para trotar.",
        "practico": "Norte. Gratis (bote aparte).",
    },
    "Parque El Chicó (Museo)": {
        "clave": "Jardines de una hacienda colonial vuelta museo: un oasis verde junto al Parque de la 93.",
        "ver": "Los jardines de la antigua hacienda y un café tranquilo.",
        "momento": "Paseo tranquilo en el norte, fácil de combinar con el Parque 93.",
        "practico": "Chicó. Económico.",
    },
    "Parque Simón Bolívar — Lago": {
        "clave": "El gran pulmón de Bogotá (más grande que Central Park): lago con botes, ciclovía y conciertos.",
        "ver": "El lago con botes y las zonas para picnic; aquí pasan los conciertos grandes.",
        "practico": "Salitre. Gratis. De día.",
    },

    # MIRADOR / EXPERIENCIA / MERCADO
    "Mirador de La Calera": {
        "clave": "El plan clásico de noche: subir la vía a La Calera para ver Bogotá iluminada, con fogatas y comida.",
        "ver": "La vista nocturna de la ciudad iluminada desde la montaña.",
        "momento": "Atardecer/noche. Hay restaurantes y puestos de comida arriba.",
        "ojo": "Es subiendo la montaña — ve en carro/app, no caminando, y de noche con la vía iluminada.",
        "practico": "La Calera (afueras). Ir en app/carro.",
    },
    "Cerro El Cable (senderismo)": {
        "clave": "Caminata exigente por los cerros con vistas a la ciudad: plan de mañana para los que quieren sudar.",
        "ver": "Las vistas de la ciudad desde la subida; sendero de montaña de verdad.",
        "momento": "TEMPRANO — suele abrir solo en la mañana, consulta horario de acceso.",
        "ojo": "Es exigente, no un paseo suave: ve con calzado adecuado, agua y temprano.",
        "practico": "Cerros orientales. Gratis. Solo mañana.",
    },
    "Mercado de las Pulgas de San Alejo": {
        "clave": "Mercado de pulgas dominical icónico: vinilos, antigüedades, monedas y curiosidades para buscar tesoros.",
        "ver": "Los puestos de vinilos, antigüedades y curiosidades; plan de domingo muy bogotano.",
        "momento": "DOMINGOS — es cuando se arma. Lleva efectivo para regatear.",
        "practico": "Centro. Gratis. Efectivo.",
    },
}
