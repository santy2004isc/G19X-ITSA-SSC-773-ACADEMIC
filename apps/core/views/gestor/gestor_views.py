import re
from functools import wraps
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import JsonResponse
from django.db import transaction
from django.db.models import Q, Func, Value
from django.db.models.functions import Lower
from django.utils import timezone
from django.views.decorators.http import require_POST

from ...models import Usuario, LeyFederal, FragmentoNormativo
from ...forms.gestor_forms import CargarDocumentoForm, EditarPerfilGestorForm, EditarLeyFederalForm
from ...services.pdf_processor import (
    procesar_pdf_en_memoria,
    extraer_metadatos_primera_pagina,
    extraer_articulo_fragmento,
    generar_vector_embedding,
    calcular_hash_fragmento
)


def _crear_fragmentos_normativos(ley, chunks):
    fragmentos = []
    fragmentos_vistos = set()
    articulo_actual = ""
    en_transitorios = False
    for chunk in chunks:
        coincidencias_articulo = []
        transitorio = re.search(r"^[ \t]*TRANSITORIOS\b", chunk, re.IGNORECASE | re.MULTILINE)
        encabezado = extraer_articulo_fragmento(chunk)
        # Si el encabezado y «TRANSITORIOS» están juntos, el artículo anterior
        # al título no pertenece a los transitorios.
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
        fragmentos.append(
            FragmentoNormativo(
                ley=ley,
                contenido_fragmento=chunk,
                articulo=articulo_actual,
                vector_embedding=generar_vector_embedding(chunk),
                hash_fragmento=calcular_hash_fragmento(chunk, ley.pk),
            )
        )
    return fragmentos


def gestor_required(view_func):
    """
    Restringe acceso a roles.
    """
    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        if not request.user.is_authenticated or request.user.rol not in [Usuario.Rol.GESTOR]:
            messages.error(request, "Acceso restringido. Se requieren permisos de Gestor de la Base de Conocimiento.")
            return redirect('login')
        return view_func(request, *args, **kwargs)
    return _wrapped_view


@login_required
@gestor_required
@require_POST
def previsualizar_documento_view(request):
    archivo = request.FILES.get("archivo_pdf")
    if not archivo:
        return JsonResponse({"error": "Selecciona un archivo PDF para analizar."}, status=400)
    if not archivo.name.lower().endswith(".pdf"):
        return JsonResponse({"error": "El archivo debe tener formato PDF."}, status=400)
    if archivo.size > 50 * 1024 * 1024:
        return JsonResponse({"error": "El PDF no puede exceder los 50 MB."}, status=400)

    try:
        archivo.seek(0)
        procesar_pdf_en_memoria(archivo)
        archivo.seek(0)
        metadatos = extraer_metadatos_primera_pagina(archivo)
    except ValidationError as error:
        return JsonResponse({"error": str(error)}, status=400)
    return JsonResponse({"metadatos": metadatos})


@login_required
@gestor_required
def gestor_dashboard_view(request):
    """
    Dashboard del Gestor con buscador por Nombre o Siglas.
    Muestra todas las leyes federales cargadas en el sistema, permitiendo
    su visualización y actualización independientemente de qué usuario las cargó.
    """
    query = request.GET.get('q', '').strip()
    ordenes_permitidos = {
        'nombre': 'nombre',
        'siglas': 'siglas',
        'publicacion': 'fecha_publicacion',
        'reforma': 'fecha_ultima_reforma',
        'cargado_por': 'usuario__nombre',
        'modificacion': 'fecha_modificacion',
        'estado': 'estado',
    }
    orden = request.GET.get('orden', '')
    direccion = request.GET.get('direccion', 'asc')
    campo_orden = ordenes_permitidos.get(orden)
    if direccion not in ('asc', 'desc'):
        direccion = 'asc'

    leyes = LeyFederal.objects.select_related('usuario', 'usuario_modificacion').all()

    if query:
        # PostgreSQL unaccent permite encontrar, por ejemplo, "Constitucion"
        # aunque el nombre almacenado contenga "Constitución".
        class Unaccent(Func):
            function = 'unaccent'
            template = "%(function)s(%(expressions)s)"
        consulta_sin_acentos = Unaccent(Value(query.lower()))
        leyes = leyes.annotate(
            nombre_sin_acentos=Unaccent(Lower('nombre')),
            siglas_sin_acentos=Unaccent(Lower('siglas')),
        ).filter(
            Q(nombre_sin_acentos__contains=consulta_sin_acentos) |
            Q(siglas_sin_acentos__contains=consulta_sin_acentos)
        )

    if campo_orden:
        prefijo_orden = '' if direccion == 'asc' else '-'
        leyes = leyes.order_by(f'{prefijo_orden}{campo_orden}', 'pk')
    else:
        orden = ''
        leyes = leyes.order_by('-fecha_creacion', 'pk')
    total_fragmentos = FragmentoNormativo.objects.count()

    context = {
        'leyes': leyes,
        'total_leyes': LeyFederal.objects.count(),
        'total_fragmentos': total_fragmentos,
        'query': query,
        'orden_actual': orden,
        'direccion_actual': direccion,
    }
    return render(request, 'gestor/dashboard.html', context)


@login_required
@gestor_required
def cargar_documento_view(request):
    """
    Carga e ingesta de PDF -> LeyFederal y FragmentoNormativo.
    """
    if request.method == 'POST':
        form = CargarDocumentoForm(request.POST, request.FILES)
        if form.is_valid():
            archivo_pdf = request.FILES['archivo_pdf']
            
            try:
                hash_archivo, hash_contenido, chunks = procesar_pdf_en_memoria(archivo_pdf)

                if LeyFederal.objects.filter(
                    Q(hash_documento=hash_archivo) | Q(hash_contenido=hash_contenido)
                ).exists():
                    messages.error(request, "El contenido de este documento ya está cargado; no es necesario volver a procesarlo.")
                    return render(request, 'gestor/cargar_documento.html', {'form': form})

                with transaction.atomic():
                    ley = form.save(commit=False)
                    ley.usuario = request.user
                    ley.hash_documento = hash_archivo
                    ley.hash_contenido = hash_contenido
                    ley.save()

                    fragmentos_objetos = _crear_fragmentos_normativos(ley, chunks)
                    FragmentoNormativo.objects.bulk_create(fragmentos_objetos)

                messages.success(request, f"La norma '{ley.nombre}' fue procesada e indexada exitosamente con {len(fragmentos_objetos)} fragmentos únicos.")
                return redirect('gestor_dashboard')

            except Exception as e:
                messages.error(request, f"Ocurrió un error al procesar el archivo: {str(e)}")
    else:
        form = CargarDocumentoForm()

    return render(request, 'gestor/cargar_documento.html', {'form': form})


@login_required
@gestor_required
def editar_ley_view(request, ley_id):
    """
    Permite actualizar metadatos y re-procesar los fragmentos vectoriales de una ley.
    """
    ley = get_object_or_404(LeyFederal, id=ley_id)
    
    if request.method == 'POST':
        form = EditarLeyFederalForm(request.POST, request.FILES, instance=ley)
        if form.is_valid():
            archivo_pdf = request.FILES.get('archivo_pdf')
            
            try:
                with transaction.atomic():
                    ley_actualizada = form.save(commit=False)
                    ley_actualizada.usuario_modificacion = request.user
                    ley_actualizada.fecha_modificacion = timezone.now()
                    
                    # Si se adjuntó un nuevo PDF, se reemplazan los fragmentos y vectores
                    if archivo_pdf:
                        hash_archivo, hash_contenido, chunks = procesar_pdf_en_memoria(archivo_pdf)

                        # Validar duplicados exceptuando la ley actual
                        if LeyFederal.objects.filter(
                            Q(hash_documento=hash_archivo) | Q(hash_contenido=hash_contenido)
                        ).exclude(id=ley.id).exists():
                            messages.error(request, "El contenido del PDF proporcionado ya está cargado en otra ley registrada.")
                            return render(request, 'gestor/editar_ley.html', {'form': form, 'ley': ley})

                        mismo_contenido = (
                            hash_contenido == ley.hash_contenido
                            or hash_archivo == ley.hash_documento
                        )
                        ley_actualizada.hash_documento = hash_archivo
                        ley_actualizada.hash_contenido = hash_contenido
                        ley_actualizada.save()

                        if mismo_contenido:
                            messages.success(
                                request,
                                f"Los datos de la ley '{ley.nombre}' fueron actualizados; el contenido no cambió y se conservaron sus fragmentos vectoriales."
                            )
                        else:
                            # Eliminar vectores/fragmentos anteriores e insertar los nuevos
                            FragmentoNormativo.objects.filter(ley=ley).delete()

                            fragmentos_objetos = _crear_fragmentos_normativos(ley, chunks)
                            FragmentoNormativo.objects.bulk_create(fragmentos_objetos)
                            messages.success(request, f"La ley '{ley.nombre}' y sus fragmentos vectoriales ({len(fragmentos_objetos)} fragmentos únicos) fueron actualizados con éxito.")
                    else:
                        ley_actualizada.save()
                        messages.success(request, f"Los datos de la ley '{ley.nombre}' fueron actualizados correctamente.")

                return redirect('gestor_dashboard')

            except Exception as e:
                messages.error(request, f"Ocurrió un error al actualizar la ley: {str(e)}")
    else:
        form = EditarLeyFederalForm(instance=ley)

    return render(request, 'gestor/editar_ley.html', {'form': form, 'ley': ley})


@login_required
@gestor_required
def perfil_gestor_view(request):
    """
    Edición de perfil del usuario Gestor.
    """
    user = request.user
    if request.method == 'POST':
        form = EditarPerfilGestorForm(request.POST, instance=user, user=user)
        if form.is_valid():
            form.save()
            messages.success(request, "Tu perfil ha sido actualizado exitosamente.")
            return redirect('gestor_perfil')
    else:
        form = EditarPerfilGestorForm(instance=user, user=user)

    return render(request, 'gestor/perfil.html', {'form': form})
