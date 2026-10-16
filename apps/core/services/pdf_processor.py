import hashlib
import io
import re
import pypdf
from django.core.exceptions import ValidationError
from sentence_transformers import SentenceTransformer

# Cargador perezoso para el modelo sentence-transformers (768 dimensiones) (RF-2.3)
_embedding_model = None

def get_embedding_model():
    global _embedding_model
    if _embedding_model is None:
        _embedding_model = SentenceTransformer('sentence-transformers/paraphrase-multilingual-mpnet-base-v2')
    return _embedding_model


def _limpiar_texto(texto):
    """
    Normaliza espacios y saltos de línea repetidos generados por la extracción de PDF.
    """
    texto = re.sub(r'\r\n|\r', '\n', texto)
    # Reemplazar múltiples saltos de línea consecutivos por un doble salto
    texto = re.sub(r'\n{3,}', '\n\n', texto)
    # Reemplazar múltiples espacios o tabuladores horizontales por uno solo
    texto = re.sub(r'[ \t]+', ' ', texto)
    return texto.strip()


def _fragmentar_texto_estructurado(texto, chunk_size=1000, overlap=150):
    """
    Fragmenta el texto legal respetando párrafos, oraciones y límites de palabras,
    evitando cortar palabras a la mitad o descontextualizar artículos (RF-2.2, RF-2.3).
    """
    # Intentar dividir primero por párrafos
    parrafos = [p.strip() for p in texto.split('\n\n') if p.strip()]
    if not parrafos:
        parrafos = [texto]

    chunks = []
    chunk_actual = ""

    for parrafo in parrafos:
        # Si agregar este párrafo no excede el tamaño máximo, acumularlo
        if not chunk_actual:
            chunk_actual = parrafo
        elif len(chunk_actual) + len(parrafo) + 2 <= chunk_size:
            chunk_actual += "\n\n" + parrafo
        else:
            # Si el párrafo actual por sí solo supera chunk_size, dividirlo por oraciones o palabras
            if len(parrafo) > chunk_size:
                # Primero guardamos lo que teníamos acumulado
                if chunk_actual:
                    chunks.append(chunk_actual.strip())
                    chunk_actual = ""

                # Dividir el párrafo largo en oraciones
                oraciones = re.split(r'(?<=[.!?])\s+', parrafo)
                sub_chunk = ""
                for oracion in oraciones:
                    if not sub_chunk:
                        sub_chunk = oracion
                    elif len(sub_chunk) + len(oracion) + 1 <= chunk_size:
                        sub_chunk += " " + oracion
                    else:
                        chunks.append(sub_chunk.strip())
                        # Mantener overlap de palabras finales si es posible
                        palabras = sub_chunk.split()
                        traslape = " ".join(palabras[-15:]) if len(palabras) > 15 else ""
                        sub_chunk = (traslape + " " + oracion).strip() if traslape else oracion

                if sub_chunk:
                    chunk_actual = sub_chunk
            else:
                # Guardamos el chunk actual y preparamos el siguiente con overlap
                chunks.append(chunk_actual.strip())
                # Overlap: tomar los últimos caracteres/palabras del chunk anterior
                palabras_previas = chunk_actual.split()
                overlap_texto = ""
                if palabras_previas:
                    # Seleccionar suficientes palabras para aproximar el overlap deseado
                    palabras_acumuladas = []
                    longitud = 0
                    for pal in reversed(palabras_previas):
                        if longitud + len(pal) + 1 <= overlap:
                            palabras_acumuladas.insert(0, pal)
                            longitud += len(pal) + 1
                        else:
                            break
                    overlap_texto = " ".join(palabras_acumuladas)

                chunk_actual = (overlap_texto + "\n\n" + parrafo).strip() if overlap_texto else parrafo

    if chunk_actual and chunk_actual.strip():
        chunks.append(chunk_actual.strip())

    return [c for c in chunks if c.strip()]


def procesar_pdf_en_memoria(file_obj, chunk_size=1000, overlap=150):
    """
    Lee el PDF en memoria, genera su hash SHA-256 y extrae fragmentos de texto limpios (RF-2.2).
    Garantiza que ningún archivo físico se escriba en el disco del servidor.
    """
    file_bytes = file_obj.read()
    
    # Cálculo del Hash SHA-256 del PDF completo
    hash_documento = hashlib.sha256(file_bytes).hexdigest()

    # Lectura del contenido sin escribir en el almacenamiento físico
    try:
        pdf_reader = pypdf.PdfReader(io.BytesIO(file_bytes))
        paginas_texto = []
        for page in pdf_reader.pages:
            texto = page.extract_text()
            if texto:
                paginas_texto.append(texto)
        texto_completo = "\n".join(paginas_texto)
    except Exception as e:
        raise ValidationError(f"Error al leer la estructura del archivo PDF: {str(e)}")

    texto_limpio = _limpiar_texto(texto_completo)
    if not texto_limpio:
        raise ValidationError("El archivo PDF no contiene texto legible o es un documento escaneado como imagen.")

    # Chunking estructurado y semántico
    chunks = _fragmentar_texto_estructurado(texto_limpio, chunk_size=chunk_size, overlap=overlap)

    return hash_documento, chunks


def generar_vector_embedding(texto):
    """
    Genera el vector embedding localmente usando sentence-transformers (RF-2.3).
    """
    model = get_embedding_model()
    embedding = model.encode(texto, convert_to_numpy=True)
    return embedding.tolist()


def calcular_hash_fragmento(texto):
    """
    Genera un hash SHA-256 del contenido del fragmento para evitar duplicaciones.
    """
    return hashlib.sha256(texto.encode('utf-8')).hexdigest()