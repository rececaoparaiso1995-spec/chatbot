"""
rececionista/templates.py — Textos multilingues (PT/EN/ES/FR).

Regra da especificação (RF-35/36): tudo o que contém FACTOS (preços,
disponibilidade, resumo de pedido) é montado aqui, de forma determinística.
O modelo de IA nunca escreve estes textos.
"""

from __future__ import annotations

from datetime import date

LANGUAGES = ("pt", "en", "es", "fr")
LANGUAGE_NAMES = {"pt": "Português", "en": "English", "es": "Español", "fr": "Français"}


def money(lang: str, value: float) -> str:
    """Formata euros no formato do idioma."""
    if lang == "en":
        return f"€{value:,.2f}"
    formatted = f"{value:,.2f}"
    formatted = formatted.replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{formatted} €"


def dmy(d: date) -> str:
    return d.strftime("%d/%m/%Y")


STRINGS: dict[str, dict[str, str]] = {
    "pt": {
        "intro": (
            "Olá! Sou o assistente virtual do Paraíso Villas & Spa. "
            "Posso ajudar com disponibilidade, preços, fotografias e informações "
            "sobre o hotel."
        ),
        "ask_dates": (
            "Para verificar disponibilidade, indique por favor as datas de entrada "
            "e saída e o número de hóspedes."
        ),
        "ask_nights": "Quantas noites pretende ficar?",
        "ask_dates_reception": (
            "Para registar o seu pedido, indique por favor as datas de entrada e "
            "saída e o número de hóspedes. A receção responde com a disponibilidade "
            "e o preço assim que abrir."
        ),
        "request_noted": (
            "Registei o seu pedido para {checkin} a {checkout} ({guests} hóspede(s), "
            "{meal}), em nome de {name}. Assim que a receção abrir, alguém entrará "
            "em contacto com a disponibilidade e o preço para essas datas. O pedido "
            "ainda não está confirmado."
        ),
        "request_ask_name": "Para registar o pedido, como se chama?",
        "request_ask_guests": "Para quantas pessoas é a estadia?",
        "request_ask_meal": (
            "Prefere com pequeno-almoço ou só alojamento?"
        ),
        "cannot_confirm": (
            "Neste momento não consigo confirmar a disponibilidade. Vou encaminhar "
            "o seu pedido para a receção, que responderá durante o dia."
        ),
        "ack_wait": "Um momento, estou a verificar a disponibilidade. 🙂",
        "no_availability": (
            "Neste momento não tenho disponibilidade para {checkin} a {checkout} "
            "({guests} hóspede(s))."
        ),
        "alternatives_intro": "Encontrei estas alternativas próximas:",
        "alt_line": "• {villa} — {checkin} a {checkout} ({nights} noite(s)) — total {total}",
        "offers_intro": "Temos disponibilidade. Valores por noite (IVA incluído):",
        "offer_header": "{villa} — {meal}, {nights} noite(s), {guests} hóspede(s)",
        "night_line": "• {date}: {price}",
        "total_line": "Total da estadia: {total} (IVA incluído)",
        "meal_bb": "alojamento com pequeno-almoço",
        "meal_ro": "alojamento sem pequeno-almoço",
        "capacity": (
            "Para {guests} hóspedes não tenho tipologia com essa capacidade. "
            "A receção poderá avaliar outras soluções."
        ),
        "booking_ask": (
            "Com certeza. Para registar o pedido, indique por favor o seu nome "
            "completo e o email de contacto."
        ),
        "booking_need": "Falta apenas: {fields}.",
        "booking_partial": (
            "Registei o seu pedido:\n{summary}\n\nFicou em falta: {fields}."
        ),
        "booking_saved": (
            "Pedido registado:\n{summary}\n\n"
            "O pedido ainda NÃO está confirmado. A receção irá verificar a "
            "disponibilidade e responder durante o dia."
        ),
        "booking_wait_dates": (
            "Com certeza. Indique por favor as datas de entrada e saída e o número "
            "de hóspedes, para eu apresentar o valor antes de registar o pedido."
        ),
        "closing": (
            "A receção retoma o atendimento dentro de momentos. Obrigado pelo seu "
            "contacto e boa noite!"
        ),
        "escalate": (
            "Para lhe dar uma resposta correta, vou encaminhar a sua mensagem para a "
            "receção, que responderá durante o dia."
        ),
        "unclear": (
            "Peço desculpa, não compreendi. Pode reformular indicando as datas de "
            "entrada e saída e o número de hóspedes?"
        ),
        "audio": (
            "Recebi uma mensagem de voz, mas não a consigo ouvir. Pode escrever, por "
            "favor? A receção responderá durante o dia."
        ),
        "photos_none": (
            "Não tenho fotografias disponíveis desse alojamento neste momento. "
            "A receção poderá enviar-lhe durante o dia."
        ),
        "photos_caption": "Fotografias de {villa} — Paraíso Villas & Spa",
        "thanks": "Ao seu dispor! Bom descanso. 🌙",
        "greeting": "Boa noite! Em que posso ajudar?",
        "doc_found": "Sobre {topic}:\n{excerpt}",
        "field_name": "nome",
        "field_email": "email",
        "field_dates": "datas de entrada e saída",
        "field_phone": "telemóvel",
    },
    "en": {
        "intro": (
            "Hello! I am the virtual assistant of Paraíso Villas & Spa. I can help "
            "with availability, rates, photos and information about the hotel."
        ),
        "ask_dates": (
            "To check availability, please share your arrival and departure dates "
            "and the number of guests."
        ),
        "ask_nights": "How many nights would you like to stay?",
        "ask_dates_reception": (
            "To register your request, please share your arrival and departure "
            "dates and the number of guests. Reception will reply with "
            "availability and rates as soon as it opens."
        ),
        "request_noted": (
            "I have registered your request from {checkin} to {checkout} "
            "({guests} guest(s), {meal}), in the name of {name}. As soon as "
            "reception opens, someone will contact you with availability and rates "
            "for those dates. The request is not yet confirmed."
        ),
        "request_ask_name": "To register the request, what is your name?",
        "request_ask_guests": "How many people is the stay for?",
        "request_ask_meal": "Would you like breakfast included or room only?",
        "cannot_confirm": (
            "I cannot confirm availability at the moment. I will forward your "
            "request to reception, who will reply during the day."
        ),
        "ack_wait": "One moment, I am checking availability. 🙂",
        "no_availability": (
            "I currently have no availability from {checkin} to {checkout} "
            "({guests} guest(s))."
        ),
        "alternatives_intro": "I found these nearby alternatives:",
        "alt_line": "• {villa} — {checkin} to {checkout} ({nights} night(s)) — total {total}",
        "offers_intro": "We have availability. Rates per night (VAT included):",
        "offer_header": "{villa} — {meal}, {nights} night(s), {guests} guest(s)",
        "night_line": "• {date}: {price}",
        "total_line": "Total for the stay: {total} (VAT included)",
        "meal_bb": "room with breakfast",
        "meal_ro": "room only",
        "capacity": (
            "I have no villa type for {guests} guests. Reception will be able to "
            "look into other options."
        ),
        "booking_ask": (
            "Certainly. To register the request, please share your full name and "
            "email address."
        ),
        "booking_need": "Still missing: {fields}.",
        "booking_partial": "I have registered your request:\n{summary}\n\nStill missing: {fields}.",
        "booking_saved": (
            "Request registered:\n{summary}\n\n"
            "The request is NOT yet confirmed. Reception will check availability "
            "and reply during the day."
        ),
        "booking_wait_dates": (
            "Certainly. Please share your arrival and departure dates and the number "
            "of guests, so I can quote the stay before registering the request."
        ),
        "closing": (
            "Reception resumes assistance shortly. Thank you for contacting us and "
            "have a good night!"
        ),
        "escalate": (
            "To give you a correct answer, I will forward your message to reception, "
            "who will reply during the day."
        ),
        "unclear": (
            "I am sorry, I did not understand. Could you rephrase with your arrival "
            "and departure dates and the number of guests?"
        ),
        "audio": (
            "I received a voice message but I cannot listen to it. Could you type it, "
            "please? Reception will reply during the day."
        ),
        "photos_none": (
            "I do not have photos of that villa available right now. Reception can "
            "send them to you during the day."
        ),
        "photos_caption": "Photos of {villa} — Paraíso Villas & Spa",
        "thanks": "My pleasure! Sleep well. 🌙",
        "greeting": "Good evening! How can I help?",
        "doc_found": "About {topic}:\n{excerpt}",
        "field_name": "name",
        "field_email": "email",
        "field_dates": "arrival and departure dates",
        "field_phone": "phone number",
    },
    "es": {
        "intro": (
            "¡Hola! Soy el asistente virtual de Paraíso Villas & Spa. Puedo ayudar "
            "con disponibilidad, precios, fotografías e información del hotel."
        ),
        "ask_dates": (
            "Para comprobar la disponibilidad, indique por favor las fechas de "
            "entrada y salida y el número de huéspedes."
        ),
        "ask_nights": "¿Cuántas noches desea alojarse?",
        "ask_dates_reception": (
            "Para registrar su solicitud, indique por favor las fechas de entrada "
            "y salida y el número de huéspedes. Recepción responderá con la "
            "disponibilidad y el precio en cuanto abra."
        ),
        "request_noted": (
            "He registrado su solicitud del {checkin} al {checkout} "
            "({guests} huésped(es), {meal}), a nombre de {name}. En cuanto abra "
            "recepción, alguien se pondrá en contacto con la disponibilidad y el "
            "precio para esas fechas. La solicitud aún no está confirmada."
        ),
        "request_ask_name": "Para registrar la solicitud, ¿cómo se llama?",
        "request_ask_guests": "¿Para cuántas personas es la estancia?",
        "request_ask_meal": "¿Prefiere con desayuno o solo alojamiento?",
        "cannot_confirm": (
            "En este momento no puedo confirmar la disponibilidad. Voy a enviar su "
            "solicitud a recepción, que responderá durante el día."
        ),
        "ack_wait": "Un momento, estoy comprobando la disponibilidad. 🙂",
        "no_availability": (
            "En este momento no tengo disponibilidad del {checkin} al {checkout} "
            "({guests} huésped(es))."
        ),
        "alternatives_intro": "He encontrado estas alternativas cercanas:",
        "alt_line": "• {villa} — {checkin} a {checkout} ({nights} noche(s)) — total {total}",
        "offers_intro": "Tenemos disponibilidad. Precios por noche (IVA incluido):",
        "offer_header": "{villa} — {meal}, {nights} noche(s), {guests} huésped(es)",
        "night_line": "• {date}: {price}",
        "total_line": "Total de la estancia: {total} (IVA incluido)",
        "meal_bb": "alojamiento con desayuno",
        "meal_ro": "solo alojamiento",
        "capacity": (
            "Para {guests} huéspedes no tengo una tipología con esa capacidad. "
            "Recepción podrá valorar otras opciones."
        ),
        "booking_ask": (
            "Por supuesto. Para registrar la solicitud, indique por favor su nombre "
            "completo y su correo electrónico."
        ),
        "booking_need": "Falta solo: {fields}.",
        "booking_partial": "He registrado su solicitud:\n{summary}\n\nFalta: {fields}.",
        "booking_saved": (
            "Solicitud registrada:\n{summary}\n\n"
            "La solicitud NO está confirmada. Recepción verificará la disponibilidad "
            "y responderá durante el día."
        ),
        "booking_wait_dates": (
            "Por supuesto. Indique las fechas de entrada y salida y el número de "
            "huéspedes, para presentar el precio antes de registrar la solicitud."
        ),
        "closing": (
            "Recepción retoma la atención en breve. ¡Gracias por su contacto y "
            "buenas noches!"
        ),
        "escalate": (
            "Para darle una respuesta correcta, voy a enviar su mensaje a recepción, "
            "que responderá durante el día."
        ),
        "unclear": (
            "Disculpe, no le he entendido. ¿Puede repetirlo indicando las fechas de "
            "entrada y salida y el número de huéspedes?"
        ),
        "audio": (
            "He recibido un mensaje de voz, pero no puedo escucharlo. ¿Puede "
            "escribirlo? Recepción responderá durante el día."
        ),
        "photos_none": (
            "No tengo fotografías disponibles de ese alojamiento en este momento. "
            "Recepción podrá enviarlas durante el día."
        ),
        "photos_caption": "Fotografías de {villa} — Paraíso Villas & Spa",
        "thanks": "¡A su disposición! Buenas noches. 🌙",
        "greeting": "¡Buenas noches! ¿En qué puedo ayudar?",
        "doc_found": "Sobre {topic}:\n{excerpt}",
        "field_name": "nombre",
        "field_email": "correo electrónico",
        "field_dates": "fechas de entrada y salida",
        "field_phone": "teléfono",
    },
    "fr": {
        "intro": (
            "Bonjour ! Je suis l'assistant virtuel du Paraíso Villas & Spa. Je peux "
            "vous aider avec les disponibilités, les tarifs, les photos et les "
            "informations sur l'hôtel."
        ),
        "ask_dates": (
            "Pour vérifier les disponibilités, merci d'indiquer vos dates d'arrivée "
            "et de départ ainsi que le nombre de personnes."
        ),
        "ask_nights": "Combien de nuits souhaitez-vous séjourner ?",
        "ask_dates_reception": (
            "Pour enregistrer votre demande, merci d'indiquer vos dates d'arrivée "
            "et de départ ainsi que le nombre de personnes. La réception répondra "
            "avec les disponibilités et le tarif dès son ouverture."
        ),
        "request_noted": (
            "J'ai enregistré votre demande du {checkin} au {checkout} "
            "({guests} personne(s), {meal}), au nom de {name}. Dès l'ouverture de "
            "la réception, quelqu'un vous contactera avec les disponibilités et le "
            "tarif pour ces dates. La demande n'est pas encore confirmée."
        ),
        "request_ask_name": "Pour enregistrer la demande, quel est votre nom ?",
        "request_ask_guests": "Pour combien de personnes est le séjour ?",
        "request_ask_meal": "Souhaitez-vous le petit-déjeuner ou la chambre seule ?",
        "cannot_confirm": (
            "Je ne peux pas confirmer les disponibilités pour le moment. Je "
            "transmets votre demande à la réception, qui répondra dans la journée."
        ),
        "ack_wait": "Un instant, je vérifie les disponibilités. 🙂",
        "no_availability": (
            "Je n'ai pas de disponibilité du {checkin} au {checkout} "
            "({guests} personne(s))."
        ),
        "alternatives_intro": "J'ai trouvé ces alternatives proches :",
        "alt_line": "• {villa} — {checkin} au {checkout} ({nights} nuit(s)) — total {total}",
        "offers_intro": "Nous avons de la disponibilité. Tarifs par nuit (TVA incluse) :",
        "offer_header": "{villa} — {meal}, {nights} nuit(s), {guests} personne(s)",
        "night_line": "• {date} : {price}",
        "total_line": "Total du séjour : {total} (TVA incluse)",
        "meal_bb": "chambre avec petit-déjeuner",
        "meal_ro": "chambre seule",
        "capacity": (
            "Je n'ai pas de typologie pour {guests} personnes. La réception pourra "
            "étudier d'autres solutions."
        ),
        "booking_ask": (
            "Bien sûr. Pour enregistrer la demande, merci d'indiquer votre nom "
            "complet et votre adresse e-mail."
        ),
        "booking_need": "Il manque seulement : {fields}.",
        "booking_partial": "J'ai enregistré votre demande :\n{summary}\n\nIl manque : {fields}.",
        "booking_saved": (
            "Demande enregistrée :\n{summary}\n\n"
            "La demande n'est PAS confirmée. La réception vérifiera la disponibilité "
            "et répondra dans la journée."
        ),
        "booking_wait_dates": (
            "Bien sûr. Indiquez vos dates d'arrivée et de départ ainsi que le nombre "
            "de personnes, afin que je présente le tarif avant l'enregistrement."
        ),
        "closing": (
            "La réception reprend l'accueil sous peu. Merci de votre contact et "
            "bonne nuit !"
        ),
        "escalate": (
            "Pour vous donner une réponse correcte, je transmets votre message à la "
            "réception, qui répondra dans la journée."
        ),
        "unclear": (
            "Je suis désolé, je n'ai pas compris. Pouvez-vous reformuler en indiquant "
            "les dates et le nombre de personnes ?"
        ),
        "audio": (
            "J'ai reçu un message vocal, mais je ne peux pas l'écouter. Pouvez-vous "
            "l'écrire ? La réception répondra dans la journée."
        ),
        "photos_none": (
            "Je n'ai pas de photos de ce logement pour le moment. La réception pourra "
            "vous les envoyer dans la journée."
        ),
        "photos_caption": "Photos de {villa} — Paraíso Villas & Spa",
        "thanks": "Je vous en prie ! Bonne nuit. 🌙",
        "greeting": "Bonsoir ! Comment puis-je vous aider ?",
        "doc_found": "À propos de {topic} :\n{excerpt}",
        "field_name": "nom",
        "field_email": "e-mail",
        "field_dates": "dates d'arrivée et de départ",
        "field_phone": "téléphone",
    },
}


def t(lang: str, key: str, **kwargs: object) -> str:
    """Devolve o texto traduzido, com fallback para português."""
    table = STRINGS.get(lang, STRINGS["pt"])
    template = table.get(key) or STRINGS["pt"][key]
    return template.format(**kwargs) if kwargs else template


def meal_label(lang: str, meal: str) -> str:
    return t(lang, "meal_bb" if meal == "BB" else "meal_ro")
