import re

from ..models import FragmentoNormativo
from .pdf_processor import (
    calcular_hash_fragmento,
    extraer_articulo_fragmento,
    generar_vectores_en_lote,
)


def crear_fragmentos_normativos(ley, chunks):
    fragmentos_vistos = set()
    articulo_actual = ""
    en_transitorios = False
    chunks_a_procesar = []
    articulos_por_chunk = []

    for chunk in chunks:
        coincidencias_articulo = []
        transitorio = re.search(r"^[ \t]*TRANSITORIOS\b", chunk, re.IGNORECASE | re.MULTILINE)
        encabezado = extraer_articulo_fragmento(chunk)
        if transitorio and encabezado:
            coincidencias_articulo = list(re.finditer(
                r"^[ \t]*(?:Artículo|ARTÍCULO)[ \t]+(?:\d+|Primero|Segundo|Tercero|Cuarto|Quinto|"
                r"Sexto|Séptimo|Octavo|Noveno|Décimo|Undécimo|Duodécimo|Único)\b|"
                r"^[ \t]*(?:Primero|Segundo|Tercero|Cuarto|Quinto|Sexto|Séptimo|Octavo|"
                r"Noveno|Décimo|Undécimo|Duodécimo|Único)[ \t]*(?:\.-|\.)",
                chunk,
                re.IGNORECASE | re.MULTILINE,
            ))
            if coincidencias_articulo:
                en_transitorios = True
        elif transitorio:
            en_transitorios = True
            if not encabezado:
                articulo_actual = ""

        articulo_detectado = extraer_articulo_fragmento(chunk)
        if articulo_detectado:
            articulo_es_transitorio = en_transitorios
            if transitorio and coincidencias_articulo and coincidencias_articulo[-1].start() < transitorio.start():
                articulo_es_transitorio = False
            articulo_actual = (
                f"{articulo_detectado} Transitorio"
                if articulo_es_transitorio
                else articulo_detectado
            )

        clave = " ".join(chunk.split())
        if clave in fragmentos_vistos:
            continue
        fragmentos_vistos.add(clave)
        chunks_a_procesar.append(chunk)
        articulos_por_chunk.append(articulo_actual)

    if not chunks_a_procesar:
        return []

    vectores = generar_vectores_en_lote(chunks_a_procesar, batch_size=32)
    return [
        FragmentoNormativo(
            ley=ley,
            contenido_fragmento=chunk,
            articulo=articulo,
            vector_embedding=vector,
            hash_fragmento=calcular_hash_fragmento(chunk, ley.pk),
        )
        for chunk, articulo, vector in zip(chunks_a_procesar, articulos_por_chunk, vectores)
    ]
