import logging

from celery import shared_task
from django.core.exceptions import ValidationError
from django.core.files.storage import default_storage
from django.db import transaction
from django.db.models import Q

from .models import FragmentoNormativo, LeyFederal
from .services.document_ingestion import crear_fragmentos_normativos
from .services.pdf_processor import procesar_pdf_en_memoria

logger = logging.getLogger(__name__)


def _eliminar_archivo_temporal(archivo_proceso):
    try:
        default_storage.delete(archivo_proceso)
    except Exception:
        logger.exception("No se pudo eliminar el archivo temporal %s", archivo_proceso)


def _marcar_fallo(ley_id, archivo_proceso, mensaje):
    actualizadas = LeyFederal.objects.filter(
        pk=ley_id,
        archivo_proceso=archivo_proceso,
    ).update(
        estado_procesamiento=LeyFederal.EstadoProcesamiento.ERROR,
        error_procesamiento=mensaje,
        archivo_proceso='',
    )
    if actualizadas:
        _eliminar_archivo_temporal(archivo_proceso)


@shared_task(bind=True, max_retries=3)
def procesar_pdf_ley(self, ley_id):
    try:
        ley = LeyFederal.objects.get(pk=ley_id)
    except LeyFederal.DoesNotExist:
        logger.warning("La ley %s ya no existe; se omite su procesamiento.", ley_id)
        return

    archivo_proceso = ley.archivo_proceso
    if not archivo_proceso or ley.estado_procesamiento == LeyFederal.EstadoProcesamiento.COMPLETADO:
        return

    LeyFederal.objects.filter(pk=ley_id, archivo_proceso=archivo_proceso).update(
        estado_procesamiento=LeyFederal.EstadoProcesamiento.PROCESANDO,
        error_procesamiento='',
    )

    try:
        with default_storage.open(archivo_proceso, 'rb') as archivo:
            hash_archivo, hash_contenido, chunks = procesar_pdf_en_memoria(archivo)

        duplicado = LeyFederal.objects.filter(
            Q(hash_documento=hash_archivo) | Q(hash_contenido=hash_contenido)
        ).exclude(pk=ley_id).exists()
        if duplicado:
            _marcar_fallo(
                ley_id,
                archivo_proceso,
                "El contenido del documento ya está registrado en otra ley.",
            )
            return

        mismo_contenido = (
            hash_contenido == ley.hash_contenido
            or hash_archivo == ley.hash_documento
        )
        fragmentos = [] if mismo_contenido else crear_fragmentos_normativos(ley, chunks)

        with transaction.atomic():
            ley_actual = LeyFederal.objects.select_for_update().get(pk=ley_id)
            if ley_actual.archivo_proceso == archivo_proceso:
                if not mismo_contenido:
                    FragmentoNormativo.objects.filter(ley=ley_actual).delete()
                    FragmentoNormativo.objects.bulk_create(fragmentos)

                ley_actual.hash_documento = hash_archivo
                ley_actual.hash_contenido = hash_contenido
                ley_actual.estado_procesamiento = LeyFederal.EstadoProcesamiento.COMPLETADO
                ley_actual.error_procesamiento = ''
                ley_actual.archivo_proceso = ''
                ley_actual.save(update_fields=[
                    'hash_documento',
                    'hash_contenido',
                    'estado_procesamiento',
                    'error_procesamiento',
                    'archivo_proceso',
                ])

        _eliminar_archivo_temporal(archivo_proceso)
    except ValidationError as error:
        mensaje = " ".join(error.messages)
        _marcar_fallo(ley_id, archivo_proceso, mensaje)
        logger.warning("PDF inválido para la ley %s: %s", ley_id, mensaje)
    except Exception as error:
        logger.exception("Error al procesar el PDF de la ley %s", ley_id)
        if self.request.retries < self.max_retries:
            raise self.retry(
                exc=error,
                countdown=min(60, 2 ** (self.request.retries + 1)),
            )

        _marcar_fallo(
            ley_id,
            archivo_proceso,
            "Ocurrió un error interno al procesar el PDF. Intenta cargarlo de nuevo.",
        )
        raise
