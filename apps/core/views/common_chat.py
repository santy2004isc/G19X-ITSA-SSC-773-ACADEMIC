import json
import logging
from uuid import UUID
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.db import transaction
from django.db.models import F

from ..models import Conversacion, Usuario
from ..services.chat_service import MAX_LONGITUD_MENSAJE, clasificar_mensaje, procesar_mensaje_chat

logger = logging.getLogger(__name__)


def ejecutar_envio_mensaje(request, es_gestor=False):
    """
    Controlador centralizado y reutilizable para el envío de mensajes por AJAX
    tanto para Gestores como para Usuarios Registrados (DRY).
    """
    usuario = request.user

    if not es_gestor:
        usuario.verificar_y_reiniciar_cuota_diaria()
        if usuario.mensajes_usados_hoy >= usuario.cuota_diaria_mensajes:
            return JsonResponse({
                'error': f'Ha alcanzado su límite diario de {usuario.cuota_diaria_mensajes} mensajes.'
            }, status=429)

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({'error': 'El cuerpo de la solicitud debe ser JSON válido.'}, status=400)

    if not isinstance(data, dict):
        return JsonResponse({'error': 'La solicitud debe contener un objeto JSON.'}, status=400)

    pregunta = data.get('mensaje')
    if not isinstance(pregunta, str):
        return JsonResponse({'error': 'El mensaje debe ser texto.'}, status=400)
    pregunta = pregunta.strip()
    if not pregunta:
        return JsonResponse({'error': 'El mensaje no puede estar vacío.'}, status=400)
    if len(pregunta) > MAX_LONGITUD_MENSAJE:
        return JsonResponse({
            'error': f'El mensaje no puede superar {MAX_LONGITUD_MENSAJE} caracteres.'
        }, status=400)

    cuota_reservada = False
    if not es_gestor and clasificar_mensaje(pregunta) == 'juridico':
        with transaction.atomic():
            reservada = Usuario.objects.filter(
                pk=usuario.pk,
                mensajes_usados_hoy__lt=F('cuota_diaria_mensajes'),
            ).update(mensajes_usados_hoy=F('mensajes_usados_hoy') + 1)
        if not reservada:
            usuario.refresh_from_db(fields=['mensajes_usados_hoy', 'cuota_diaria_mensajes'])
            return JsonResponse({
                'error': f'Ha alcanzado su límite diario de {usuario.cuota_diaria_mensajes} mensajes.'
            }, status=429)
        usuario.refresh_from_db(fields=['mensajes_usados_hoy'])
        cuota_reservada = True

    conversacion_id = data.get('conversacion_id')
    if conversacion_id is not None and not isinstance(conversacion_id, str):
        return JsonResponse({'error': 'El identificador de conversación no es válido.'}, status=400)
    if conversacion_id:
        try:
            conversacion_id = UUID(conversacion_id)
        except ValueError:
            return JsonResponse({'error': 'El identificador de conversación no es válido.'}, status=400)

    try:
        if conversacion_id:
            conversacion = get_object_or_404(Conversacion, id=conversacion_id, usuario=usuario)
        else:
            titulo_auto = pregunta[:40] + ("..." if len(pregunta) > 40 else "")
            conversacion = Conversacion(usuario=usuario, titulo=titulo_auto)

        titulos_defecto = ("Consulta Normativa", "Consulta Normativa (Gestor)")
        if not conversacion._state.adding and not conversacion.mensajes.exists() and conversacion.titulo in titulos_defecto:
            conversacion.titulo = pregunta[:40] + ("..." if len(pregunta) > 40 else "")
            conversacion.save()

        msg_usuario, msg_sistema, citas = procesar_mensaje_chat(
            conversacion,
            pregunta,
            usuario,
            consumir_cuota=False,
        )

        if es_gestor:
            url_conversacion = reverse('gestor_chat_conversacion', kwargs={'conversacion_id': conversacion.id})
            mensajes_restantes = "Ilimitado"
        else:
            url_conversacion = reverse('chat_conversacion', kwargs={'conversacion_id': conversacion.id})
            mensajes_restantes = max(0, usuario.cuota_diaria_mensajes - usuario.mensajes_usados_hoy)

        return JsonResponse({
            'ok': True,
            'conversacion_id': str(conversacion.id),
            'conversacion_url': url_conversacion,
            'conversacion_titulo': conversacion.titulo,
            'mensajes_restantes': mensajes_restantes,
            'pregunta': {
                'id': msg_usuario.id,
                'contenido': msg_usuario.contenido_texto,
                'fecha': msg_usuario.fecha_envio.strftime('%H:%M')
            },
            'respuesta': {
                'id': msg_sistema.id,
                'contenido': msg_sistema.contenido_texto,
                'proveedor': msg_sistema.proveedor_ia,
                'conmutado': msg_sistema.conmutado_fallback,
                'citas': citas,
                'fecha': msg_sistema.fecha_envio.strftime('%H:%M')
            }
        })

    except Http404:
        if cuota_reservada:
            Usuario.objects.filter(pk=usuario.pk, mensajes_usados_hoy__gt=0).update(
                mensajes_usados_hoy=F('mensajes_usados_hoy') - 1
            )
        return JsonResponse({'error': 'La conversación no existe.'}, status=404)
    except Exception:
        if cuota_reservada:
            Usuario.objects.filter(pk=usuario.pk, mensajes_usados_hoy__gt=0).update(
                mensajes_usados_hoy=F('mensajes_usados_hoy') - 1
            )
        rol_desc = "gestor" if es_gestor else "usuario"
        logger.exception(f"Error interno al procesar un mensaje de {rol_desc}.")
        return JsonResponse(
            {'error': 'Ocurrió un error interno al procesar tu mensaje. Inténtalo de nuevo.'},
            status=500,
        )
