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


def extraer_metadatos_primera_pagina(file_obj):
    """Extrae sugerencias de metadatos desde la primera página del PDF."""
    try:
        reader = pypdf.PdfReader(io.BytesIO(file_obj.read()))
        if not reader.pages:
            raise ValidationError("El PDF no contiene páginas.")
        texto = reader.pages[0].extract_text() or ""
    except ValidationError:
        raise
    except Exception as error:
        raise ValidationError(f"No se pudo leer la primera página del PDF: {error}")

    lineas = [re.sub(r"\s+", " ", linea).strip() for linea in texto.splitlines()]
    lineas = [linea for linea in lineas if linea]
    titulo = next(
        (
            linea for linea in lineas[:15]
            if re.search(r"\b(ley|c[oó]digo|constituci[oó]n|reglamento|decreto)\b", linea, re.I)
            and len(linea) <= 500
        ),
        "",
    )

    nombre_archivo = re.sub(r"\.pdf$", "", file_obj.name, flags=re.I)
    nombre_archivo = re.sub(r"[_-]+", " ", nombre_archivo).strip()
    nombre = titulo or nombre_archivo

    siglas = next(iter(re.findall(r"\(([A-ZÁÉÍÓÚÜÑ]{2,10})\)", texto)), "")

    meses = {
        "enero": "01", "febrero": "02", "marzo": "03", "abril": "04",
        "mayo": "05", "junio": "06", "julio": "07", "agosto": "08",
        "septiembre": "09", "setiembre": "09", "octubre": "10",
        "noviembre": "11", "diciembre": "12",
    }

    def fechas_en(texto_fecha):
        fechas = []
        for match in re.finditer(
            r"\b(\d{1,2})\s+de\s+([a-záéíóú]+)\s+de\s+(\d{4})\b",
            texto_fecha,
            re.I,
        ):
            mes = meses.get(match.group(2).lower())
            if mes:
                fechas.append((match.start(), f"{match.group(3)}-{mes}-{int(match.group(1)):02d}"))
        for match in re.finditer(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b", texto_fecha):
            dia, mes, anio = match.groups()
            if 1 <= int(mes) <= 12 and 1 <= int(dia) <= 31:
                fechas.append((match.start(), f"{anio}-{int(mes):02d}-{int(dia):02d}"))
        return sorted(fechas)

    fechas = fechas_en(texto)
    fecha_publicacion = ""
    fecha_reforma = ""
    if fechas:
        fecha_publicacion = fechas[0][1]
        for coincidencia in re.finditer(
            r"publicad[oa]|publicaci[oó]n|diario oficial|d\.?\s*o\.?\s*f\.?",
            texto,
            re.I,
        ):
            fecha_publicacion = min(
                fechas,
                key=lambda fecha: abs(fecha[0] - coincidencia.start()),
            )[1]
            break
        for coincidencia in re.finditer(r"reform", texto, re.I):
            fecha_cercana = min(fechas, key=lambda fecha: abs(fecha[0] - coincidencia.start()))
            fecha_reforma = fecha_cercana[1]
            break

    estado = "ABROGADA" if re.search(r"\babrogad[ao]s?\b", texto, re.I) else ""
    return {
        "nombre": nombre[:500],
        "siglas": siglas[:50],
        "fecha_publicacion": fecha_publicacion,
        "fecha_ultima_reforma": fecha_reforma,
        "estado": estado,
    }


def generar_vector_embedding(texto):
    """
    Genera el vector embedding localmente usando sentence-transformers (RF-2.3).
    """
    model = get_embedding_model()
    embedding = model.encode(texto, convert_to_numpy=True)
    return embedding.tolist()


def calcular_hash_fragmento(texto, ley_id=None):
    """Genera una huella estable que evita duplicados dentro de la misma ley."""
    texto_normalizado = " ".join(texto.split())
    contenido = f"{ley_id}:{texto_normalizado}" if ley_id is not None else texto_normalizado
    return hashlib.sha256(contenido.encode("utf-8")).hexdigest()