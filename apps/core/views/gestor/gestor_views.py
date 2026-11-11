import logging
from functools import wraps
from uuid import uuid4
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.files.storage import default_storage
from django.http import JsonResponse
from django.db import transaction
from django.core.paginator import Paginator
from django.utils import timezone
from django.views.decorators.http import require_POST

from ...models import Usuario, LeyFederal, FragmentoNormativo
from ...forms.gestor_forms import CargarDocumentoForm, EditarPerfilGestorForm, EditarLeyFederalForm
from ...services.pdf_processor import (
    extraer_metadatos_primera_pagina,
)
from ...tasks import procesar_pdf_ley

logger = logging.getLogger(__name__)


def _eliminar_pdf_temporal(archivo_proceso):
    try:
        default_storage.delete(archivo_proceso)
    except Exception:
        logger.exception("No se pudo eliminar el archivo temporal %s", archivo_proceso)


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
        metadatos = extraer_metadatos_primera_pagina(archivo)
    except ValidationError as error:
        return JsonResponse({"error": str(error)}, status=400)
    except Exception:
        logger.exception("Error inesperado al previsualizar el PDF %r", archivo.name)
        return JsonResponse(
            {"error": "Ocurrió un error interno al leer el PDF. Revisa el registro del servidor para ver el detalle."},
            status=500,
        )
    return JsonResponse({"metadatos": metadatos})


@login_required
@gestor_required
def gestor_dashboard_view(request):
    """
    Dashboard paginado del Gestor con buscador por Nombre o Siglas.
    """
    leyes = LeyFederal.objects.select_related('usuario', 'usuario_modificacion').order_by('-fecha_creacion', 'pk')
    page_obj = Paginator(leyes, 25).get_page(request.GET.get('page'))
    total_fragmentos = FragmentoNormativo.objects.count()

    context = {
        'leyes': page_obj.object_list,
        'page_obj': page_obj,
        'total_leyes': LeyFederal.objects.count(),
        'total_fragmentos': total_fragmentos,
    }
    return render(request, 'gestor/dashboard.html', context)


@login_required
@gestor_required
@require_POST
def eliminar_ley_view(request, ley_id):
    """Elimina definitivamente una ley y sus fragmentos asociados."""
    ley = get_object_or_404(LeyFederal, pk=ley_id)
    nombre_ley = ley.nombre
    with transaction.atomic():
        ley.delete()  # FragmentoNormativo.ley usa on_delete=CASCADE.
    messages.success(request, f"La ley '{nombre_ley}' y sus fragmentos fueron eliminados definitivamente.")
    return redirect('gestor_dashboard')


@login_required
@gestor_required
def cargar_documento_view(request):
    """
    Guarda la solicitud de ingesta y delega el procesamiento a Celery.
    """
    if request.method == 'POST':
        form = CargarDocumentoForm(request.POST, request.FILES)
        if form.is_valid():
            archivo_pdf = request.FILES['archivo_pdf']
            archivo_proceso = None
            ley = form.save(commit=False)
            try:
                archivo_proceso = default_storage.save(
                    f"pdf_ingesta/{uuid4().hex}.pdf",
                    archivo_pdf,
                )
                ley.usuario = request.user
                ley.hash_documento = None
                ley.archivo_proceso = archivo_proceso
                ley.estado_procesamiento = LeyFederal.EstadoProcesamiento.PENDIENTE
                ley.error_procesamiento = ''
                with transaction.atomic():
                    ley.save()
                procesar_pdf_ley.delay(ley.pk)
                messages.success(
                    request,
                    f"La norma '{ley.nombre}' se guardó y su procesamiento continuará en segundo plano.",
                )
                return redirect('gestor_dashboard')
            except Exception:
                logger.exception("No se pudo encolar el procesamiento del PDF %r", archivo_pdf.name)
                if ley.pk:
                    LeyFederal.objects.filter(pk=ley.pk).update(
                        estado_procesamiento=LeyFederal.EstadoProcesamiento.ERROR,
                        error_procesamiento="No fue posible iniciar el procesamiento en segundo plano.",
                        archivo_proceso='',
                    )
                if archivo_proceso:
                    _eliminar_pdf_temporal(archivo_proceso)
                messages.error(request, "No fue posible iniciar el procesamiento del PDF. Inténtalo nuevamente.")
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
                archivo_proceso = None
                if archivo_pdf:
                    archivo_proceso = default_storage.save(
                        f"pdf_ingesta/{uuid4().hex}.pdf",
                        archivo_pdf,
                    )

                with transaction.atomic():
                    ley_actualizada = form.save(commit=False)
                    ley_actualizada.usuario_modificacion = request.user
                    ley_actualizada.fecha_modificacion = timezone.now()
                    if archivo_proceso:
                        ley_actualizada.archivo_proceso = archivo_proceso
                        ley_actualizada.estado_procesamiento = LeyFederal.EstadoProcesamiento.PENDIENTE
                        ley_actualizada.error_procesamiento = ''
                    ley_actualizada.save()

                if archivo_proceso:
                    procesar_pdf_ley.delay(ley_actualizada.pk)
                    messages.success(
                        request,
                        f"La ley '{ley.nombre}' se actualizó y el PDF se reprocesará en segundo plano.",
                    )
                else:
                    messages.success(request, f"Los datos de la ley '{ley.nombre}' fueron actualizados correctamente.")
                return redirect('gestor_dashboard')
            except Exception:
                logger.exception("Error al actualizar la ley %s", ley.pk)
                if archivo_proceso:
                    LeyFederal.objects.filter(pk=ley.pk).update(
                        estado_procesamiento=LeyFederal.EstadoProcesamiento.ERROR,
                        error_procesamiento="No fue posible iniciar el procesamiento en segundo plano.",
                        archivo_proceso='',
                    )
                    _eliminar_pdf_temporal(archivo_proceso)
                messages.error(request, "Ocurrió un error al actualizar la ley. Revisa los datos e inténtalo nuevamente.")
                return redirect('gestor_dashboard')
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
