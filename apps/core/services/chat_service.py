import logging
import os
import re
import time
import unicodedata
import groq
from google import genai
from google.genai.errors import APIError
from django.conf import settings
from django.contrib.postgres.search import SearchVector, SearchQuery, SearchRank
from django.db import transaction
from pgvector.django import CosineDistance
from ..models import FragmentoNormativo, LeyFederal, Mensaje, RegistroFalloAPI
from .pdf_processor import generar_vector_embedding  

logger = logging.getLogger(__name__)

MENSAJE_SATURACION_APIS = (
    "En este momento los servicios de consulta inteligente se encuentran "
    "temporalmente saturados debido a la alta demanda en los proveedores externos de IA. "
    "Por favor, intenta formular tu pregunta nuevamente en unos momentos."
)

MAX_LONGITUD_MENSAJE = 4000

_MENSAJE_CORTESIA = re.compile(
    r"^(?:gracias|muchas gracias|te agradezco|entendido|"
    r"de acuerdo|perfecto|ok|okay|adios|hasta luego|nos vemos)$"
)


def clasificar_mensaje(pregunta_usuario):
    """Clasifica mensajes sencillos sin consumir IA ni consultar la base normativa."""
    normalizado = unicodedata.normalize("NFKD", pregunta_usuario.lower())
    normalizado = "".join(
        caracter for caracter in normalizado
        if not unicodedata.combining(caracter)
    )
    normalizado = re.sub(r"[^\w\s]", " ", normalizado)
    normalizado = " ".join(normalizado.split())

    if re.fullmatch(
        r"(?:hola|buenas(?: tardes| dias| noches)?|buenos dias|buen dia|"
        r"saludos|que tal|hey|holi)(?: (?:hola|buenas(?: tardes| dias| noches)?|"
        r"buenos dias|buen dia|saludos|que tal|hey|holi))?"
        r"(?: (?:que tal|como estas|espero que estes bien))?",
        normalizado,
    ):
        return "saludo"

    if _MENSAJE_CORTESIA.fullmatch(normalizado):
        return "cortesia"

    return "juridico"


def respuesta_sin_consulta_normativa(tipo_mensaje):
    if tipo_mensaje == "saludo":
        return (
            "¡Hola! ¿En qué puedo ayudarte? Puedo orientarte sobre leyes federales "
            "de México; cuéntame tu duda jurídica."
        )
    if tipo_mensaje == "cortesia":
        return "¡Con gusto! Si tienes otra duda sobre leyes federales de México, aquí estoy para ayudarte."
    return (
        "Puedo ayudarte con dudas sobre leyes federales de México. Cuéntame qué "
        "situación jurídica quieres consultar y te orientaré."
    )


def get_groq_client():
    """
    Instancia el cliente de Groq utilizando settings de Django con timeout estricto de 5s (RNF-04).
    """
    api_key = getattr(settings, 'GROQ_API_KEY', None) or os.getenv("GROQ_API_KEY", "")
    return groq.Groq(api_key=api_key, timeout=5.0)


def get_gemini_client():
    """
    Instancia el cliente de Gemini utilizando settings de Django.
    """
    api_key = getattr(settings, 'GEMINI_API_KEY', None) or os.getenv("GEMINI_API_KEY", "")
    return genai.Client(api_key=api_key)


def buscar_contexto_normativo(pregunta_usuario, top_k=4):
    """
    Búsqueda híbrida (Semántica + Léxica) con fusión de rangos recíprocos (RRF).
    Combina la similitud de coseno en pgvector con el motor de búsqueda léxica
    Full-Text Search de PostgreSQL, optimizado para artículos y conceptos jurídicos.
    Filtra ÚNICAMENTE leyes con estado VIGENTE.
    """
    vector_pregunta = generar_vector_embedding(pregunta_usuario)
    candidatos_k = max(top_k * 3, 10)
    
    candidatos_dict = {}
    rrf_scores = {}
    k_rrf = 60

    # 1. Recuperación Vectorial (Semántica mediante CosineDistance y HNSW)
    try:
        qs_vectorial = FragmentoNormativo.objects.select_related('ley').filter(
            ley__estado=LeyFederal.Estado.VIGENTE
        ).annotate(
            distancia=CosineDistance('vector_embedding', vector_pregunta)
        ).order_by('distancia')[:candidatos_k]

        for rank, frag in enumerate(qs_vectorial):
            frag_id = frag.id
            candidatos_dict[frag_id] = frag
            rrf_scores[frag_id] = rrf_scores.get(frag_id, 0.0) + (1.0 / (k_rrf + (rank + 1)))
    except Exception:
        logger.exception("Falló la recuperación vectorial de fragmentos normativos.")

    # 2. Recuperación Léxica / Full-Text Search (Exactitud en artículos y términos legales)
    try:
        search_query = SearchQuery(pregunta_usuario, config='spanish', search_type='websearch')
        search_vector = SearchVector('contenido_fragmento', config='spanish')

        qs_lexica = FragmentoNormativo.objects.select_related('ley').filter(
            ley__estado=LeyFederal.Estado.VIGENTE
        ).annotate(
            rank_lex=SearchRank(search_vector, search_query)
        ).filter(
            rank_lex__gt=0.01
        ).order_by('-rank_lex')[:candidatos_k]

        for rank, frag in enumerate(qs_lexica):
            frag_id = frag.id
            candidatos_dict[frag_id] = frag
            # Se otorga un multiplicador de 1.2 a la coincidencia léxica exacta (ej. números de artículo o siglas)
            rrf_scores[frag_id] = rrf_scores.get(frag_id, 0.0) + (1.2 / (k_rrf + (rank + 1)))
    except Exception:
        logger.exception("Falló la recuperación léxica de fragmentos normativos.")

    # 3. Selección y ordenamiento por puntuación RRF combinada
    ids_ordenados = sorted(rrf_scores.keys(), key=lambda fid: rrf_scores[fid], reverse=True)[:top_k]
    resultados = [candidatos_dict[fid] for fid in ids_ordenados]

    contexto_texto = ""
    citas = []

    for fragmento in resultados:
        ley = fragmento.ley
        referencia_articulo = (
            f" · Art. {fragmento.articulo}" if fragmento.articulo else ""
        )
        contexto_texto += (
            f"\n--- LEY: {ley.nombre} ({ley.siglas or 'S/S'})"
            f"{referencia_articulo} ---\n{fragmento.contenido_fragmento}\n"
        )
        
        cita_info = {
            'ley': ley.nombre,
            'siglas': ley.siglas or '',
            'articulo': fragmento.articulo,
            'fecha_reforma': ley.fecha_ultima_reforma.strftime('%d/%m/%Y') if ley.fecha_ultima_reforma else 'Sin reforma registrada'
        }
        if cita_info not in citas:
            citas.append(cita_info)

    return contexto_texto, citas


def procesar_mensaje_chat(conversacion, pregunta, usuario, consumir_cuota=False):
    """Genera una respuesta fuera de la transacción y persiste el intercambio de forma atómica."""
    tipo_mensaje = clasificar_mensaje(pregunta)
    if tipo_mensaje in ('saludo', 'cortesia'):
        respuesta_texto = respuesta_sin_consulta_normativa(tipo_mensaje)
        proveedor_usado = None
        conmutado = False
        citas = []
    else:
        contexto, citas = buscar_contexto_normativo(pregunta)
        respuesta_texto, proveedor_usado, conmutado = generar_respuesta_ia_con_fallback(
            pregunta_usuario=pregunta,
            contexto_normativo=contexto,
            usuario=usuario,
        )

    with transaction.atomic():
        mensaje_usuario = Mensaje.objects.create(
            conversacion=conversacion,
            emisor=Mensaje.Emisor.USUARIO,
            contenido_texto=pregunta,
        )
        mensaje_sistema = Mensaje.objects.create(
            conversacion=conversacion,
            emisor=Mensaje.Emisor.SISTEMA,
            contenido_texto=respuesta_texto,
            proveedor_ia=proveedor_usado,
            conmutado_fallback=conmutado,
            citas=citas,
        )
        if consumir_cuota and tipo_mensaje == 'juridico':
            usuario.mensajes_usados_hoy += 1
            usuario.save(update_fields=['mensajes_usados_hoy'])

    return mensaje_usuario, mensaje_sistema, citas


def generar_respuesta_ia_con_fallback(pregunta_usuario, contexto_normativo, usuario=None, modelo_solicitado=None):
    """
    Procesa la llamada a Groq y conmuta a Gemini si falla (RF-3.2, RF-3.3).
    Registra la auditoría del fallo en RegistroFalloAPI.
    Si ambos proveedores fallan por saturación/límite de cuota, devuelve
    una notificación amigable sin romper la sesión del usuario.
    """
    if not contexto_normativo.strip():
        return (
            "No se encontró información suficiente en las Leyes Federales vigentes para responder a su consulta.",
            Mensaje.ProveedorIA.GROQ,
            False
        )

    system_prompt = (
        "Eres un asistente legal experto en la legislación federal vigente de México. "
        "Tu objetivo es responder a la pregunta del usuario utilizando EXCLUSIVAMENTE el contexto normativo proporcionado.\n\n"
        "REGLA DE FILTRO / GUARDRAIL DE CONTEXTO:\n"
        "Si la consulta del usuario es ajena al marco legal mexicano, intenta tratar temas jurídicos de otros países, "
        "o no está fundamentada en el contexto normativo proporcionado, debes rechazarla respondiendo "
        "ÚNICAMENTE con el siguiente mensaje estandarizado:\n"
        "\"Lo siento, únicamente puedo responder consultas relacionadas con el marco legal mexicano y fundamentadas en el contexto normativo disponible. Por favor, formula una pregunta dentro de este ámbito.\"\n\n"
        "PAUTAS DE RESPUESTA:\n"
        "1. Explica los conceptos jurídicos en un lenguaje natural, claro, accesible y profesional.\n"
        "2. Menciona explícitamente el artículo del contexto normativo en que apoyas la respuesta; "
        "los encabezados indican la ley y el número de artículo. Cita literalmente el texto pertinente "
        "cuando ayude a fundamentar la explicación y no inventes referencias.\n"
        "3. No menciones la vigencia o estado de las leyes, ya que todas las normas proporcionadas están vigentes."
    )

    user_prompt = f"Contexto Normativo Vigente:\n{contexto_normativo}\n\nPregunta del usuario: {pregunta_usuario}"

    proveedor_usado = Mensaje.ProveedorIA.GROQ
    conmutado = False

    # 1. INTENTO PRINCIPAL: GROQ CLOUD (Prioritario, timeout <= 5s)
    try:
        groq_client = get_groq_client()
        modelo_groq = modelo_solicitado if isinstance(modelo_solicitado, str) else settings.GROQ_MODEL
        
        response = groq_client.chat.completions.create(
            model=modelo_groq,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.3,
            max_tokens=1000
        )
        return response.choices[0].message.content, proveedor_usado, conmutado

    except Exception as e_groq:
        # 2. FALLBACK AUTOMÁTICO A GOOGLE GEMINI (Respaldo por Rate Limit o Timeout)
        proveedor_usado = Mensaje.ProveedorIA.GEMINI
        conmutado = True
        codigo_error_groq = type(e_groq).__name__
        
        for intento in range(2):
            try:
                gemini_client = get_gemini_client()
                response = gemini_client.models.generate_content(
                    model=settings.GEMINI_MODEL,
                    contents=user_prompt,
                    config={
                        "system_instruction": system_prompt,
                        "temperature": 0.3,
                    }
                )
                
                # Registro de auditoría del fallback exitoso (RF-3.3, RNF-04)
                RegistroFalloAPI.objects.create(
                    usuario=usuario,
                    proveedor_solicitado=Mensaje.ProveedorIA.GROQ,
                    codigo_error=str(codigo_error_groq)[:50],
                    conmutado_a=Mensaje.ProveedorIA.GEMINI,
                    exitoso=True
                )
                
                return response.text, proveedor_usado, conmutado

            except Exception as e_gemini:
                if intento == 0:
                    time.sleep(1)
                    continue
                
                # Ambos fallaron: auditoría de fallo no exitoso
                RegistroFalloAPI.objects.create(
                    usuario=usuario,
                    proveedor_solicitado=Mensaje.ProveedorIA.GROQ,
                    codigo_error=str(codigo_error_groq)[:50],
                    conmutado_a=Mensaje.ProveedorIA.GEMINI,
                    exitoso=False
                )
                
                # Notificación controlada y amigable al usuario (RF-3.3) sin romper la sesión ni provocar error 500
                return MENSAJE_SATURACION_APIS, Mensaje.ProveedorIA.GEMINI, True