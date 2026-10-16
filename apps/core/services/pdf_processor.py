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
    """Divide todo el texto en fragmentos acotados, conservando párrafos y oraciones."""
    if chunk_size < 1:
        raise ValueError("chunk_size debe ser mayor que cero.")

    def dividir_largo(unidad):
        if len(unidad) <= chunk_size:
            return [unidad]

        partes = []
        for oracion in re.split(r'(?<=[.!?])\s+', unidad):
            if len(oracion) <= chunk_size:
                partes.append(oracion)
                continue

            parte = ""
            for palabra in oracion.split():
                palabras = [palabra[i:i + chunk_size] for i in range(0, len(palabra), chunk_size)]
                for segmento in palabras:
                    candidato = f"{parte} {segmento}".strip()
                    if parte and len(candidato) > chunk_size:
                        partes.append(parte)
                        parte = segmento
                    else:
                        parte = candidato
            if parte:
                partes.append(parte)
        return partes

    chunks = []
    chunk_actual = ""
    for parrafo in (p.strip() for p in texto.split("\n\n") if p.strip()):
        partes = dividir_largo(parrafo)
        separador = "\n\n"
        for parte in partes:
            separador_actual = separador if chunk_actual else ""
            candidato = f"{chunk_actual}{separador_actual}{parte}".strip()
            if len(candidato) <= chunk_size:
                chunk_actual = candidato
                separador = " "
                continue

            if chunk_actual:
                chunks.append(chunk_actual)
                palabras_previas = chunk_actual.split()
                traslape = ""
                longitud = 0
                for palabra in reversed(palabras_previas):
                    if longitud + len(palabra) + 1 > overlap:
                        break
                    traslape = f"{palabra} {traslape}".strip()
                    longitud += len(palabra) + 1
                espacio_disponible = chunk_size - len(parte) - 1
                if espacio_disponible > 0 and traslape:
                    traslape = traslape[-espacio_disponible:].lstrip()
                    if " " in traslape:
                        traslape = traslape.split(" ", 1)[1]
                else:
                    traslape = ""
                chunk_actual = f"{traslape} {parte}".strip() if traslape else parte
            else:
                chunk_actual = parte
            separador = " "

    if chunk_actual:
        chunks.append(chunk_actual)
    return chunks


def procesar_pdf_en_memoria(file_obj, chunk_size=1000, overlap=150):
    """
    Lee el PDF en memoria, genera su hash SHA-256 y extrae fragmentos de texto limpios (RF-2.2).
    Garantiza que ningún archivo físico se escriba en el disco del servidor.
    """
    file_bytes = file_obj.read()
    
    # Se conserva el hash binario para detectar duplicados históricos exactos.
    hash_archivo = hashlib.sha256(file_bytes).hexdigest()

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

    # El hash del contenido ignora cambios de metadatos del PDF y saltos de línea.
    hash_contenido = hashlib.sha256(texto_limpio.encode("utf-8")).hexdigest()
    chunks = _fragmentar_texto_estructurado(texto_limpio, chunk_size=chunk_size, overlap=overlap)

    return hash_archivo, hash_contenido, chunks


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