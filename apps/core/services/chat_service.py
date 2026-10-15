import os
import time
import groq
from google import genai
from google.genai.errors import APIError
from pgvector.django import CosineDistance
from ..models import FragmentoNormativo, LeyFederal, Mensaje, RegistroFalloAPI
from .pdf_processor import generar_vector_embedding  

# Configuración de clientes
groq_client = groq.Groq(api_key=os.getenv("GROQ_API_KEY"))
gemini_client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

MODELO_GROQ_DEFAULT = "openai/gpt-oss-120b" 
MODELO_GEMINI_DEFAULT = "gemini-3.8-flash"


def buscar_contexto_normativo(pregunta_usuario, top_k=4):
    """
    Busca los K fragmentos con menor distancia de coseno filtrando
    ÚNICAMENTE leyes con estado VIGENTE.
    """
    vector_pregunta = generar_vector_embedding(pregunta_usuario)
    
    resultados = FragmentoNormativo.objects.select_related('ley').filter(
        ley__estado=LeyFederal.Estado.VIGENTE
    ).annotate(
        distancia=CosineDistance('vector_embedding', vector_pregunta)
    ).order_by('distancia')[:top_k]

    contexto_texto = ""
    citas = []

    for fragmento in resultados:
        ley = fragmento.ley
        contexto_texto += f"\n--- LEY: {ley.nombre} ({ley.siglas or 'S/S'}) ---\n{fragmento.contenido_fragmento}\n"
        
        cita_info = {
            'ley': ley.nombre,
            'siglas': ley.siglas or '',
            'fecha_reforma': ley.fecha_ultima_reforma.strftime('%d/%m/%Y') if ley.fecha_ultima_reforma else 'Sin reforma registrada'
        }
        if cita_info not in citas:
            citas.append(cita_info)

    return contexto_texto, citas

def generar_respuesta_ia_con_fallback(pregunta_usuario, contexto_normativo, usuario=None, modelo_solicitado=None):
    """
    Procesa la llamada a Groq y conmuta a Gemini si falla.
    Registra la auditoría del fallo en RegistroFalloAPI.
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
        "2. Evita copiar texto literal o usar jerga excesivamente compleja salvo que sea estrictamente necesario.\n"
        "3. No menciones la vigencia o estado de las leyes, ya que todas las normas proporcionadas están vigentes."
    )

    user_prompt = f"Contexto Normativo Vigente:\n{contexto_normativo}\n\nPregunta del usuario: {pregunta_usuario}"

    proveedor_usado = Mensaje.ProveedorIA.GROQ
    conmutado = False

    # 1. INTENTO PRINCIPAL: GROQ CLOUD
    try:
        modelo_groq = modelo_solicitado if isinstance(modelo_solicitado, str) else MODELO_GROQ_DEFAULT
        
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
        # 2. FALLBACK A GOOGLE GEMINI
        proveedor_usado = Mensaje.ProveedorIA.GEMINI
        conmutado = True
        codigo_error_groq = type(e_groq).__name__
        
        for intento in range(2):
            try:
                response = gemini_client.models.generate_content(
                    model=MODELO_GEMINI_DEFAULT,
                    contents=user_prompt,
                    config={
                        "system_instruction": system_prompt,
                        "temperature": 0.3,
                    }
                )
                
             
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
                
                
                RegistroFalloAPI.objects.create(
                    usuario=usuario,
                    proveedor_solicitado=Mensaje.ProveedorIA.GROQ,
                    codigo_error=str(codigo_error_groq)[:50],
                    conmutado_a=Mensaje.ProveedorIA.GEMINI,
                    exitoso=False
                )
                
                raise RuntimeError(f"Fallo en generación de IA: Groq ({str(e_groq)}) | Gemini ({str(e_gemini)})")



            