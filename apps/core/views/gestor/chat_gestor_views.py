import json
import logging
from uuid import UUID
from django.http import Http404
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.contrib import messages
from django.urls import reverse

from ...models import Usuario, Conversacion
from ...decorators import roles_requeridos
from ...services.chat_service import (
    MAX_LONGITUD_MENSAJE,
    procesar_mensaje_chat,
)

logger = logging.getLogger(__name__)


@login_required
@roles_requeridos(Usuario.Rol.GESTOR)
def chat_gestor_interfaz_view(request, conversacion_id=None):
    usuario = request.user
    conversaciones = Conversacion.objects.filter(
        usuario=usuario,
        mensajes__isnull=False,
    ).distinct().order_by('-fecha_creacion')
    
    conversacion_actual = None
    mensajes = []

    if conversacion_id:
        conversacion_actual = get_object_or_404(Conversacion, id=conversacion_id, usuario=usuario)
    elif request.GET.get('nueva') != '1' and conversaciones.exists():
        conversacion_actual = conversaciones.first()

    if conversacion_actual:
        mensajes = conversacion_actual.mensajes.all().order_by('fecha_envio')

    context = {
        'conversaciones': conversaciones,
        'conversacion_actual': conversacion_actual,
        'mensajes': mensajes,
        'mensajes_restantes': "Ilimitado"
    }
    return render(request, 'gestor/chat.html', context)


@login_required
@roles_requeridos(Usuario.Rol.GESTOR)
@require_POST
def crear_nueva_conversacion_gestor_view(request):
    return redirect(f"{reverse('gestor_chat_interfaz')}?nueva=1")


@login_required
@roles_requeridos(Usuario.Rol.GESTOR)
@require_POST
def eliminar_conversacion_gestor_view(request, conversacion_id):
    conv = get_object_or_404(Conversacion, id=conversacion_id, usuario=request.user)
    conv.delete()
    messages.success(request, "La conversación fue eliminada.")
    return redirect('gestor_chat_interfaz')


@login_required
@roles_requeridos(Usuario.Rol.GESTOR)
@require_POST
def enviar_mensaje_gestor_api_view(request):
    usuario = request.user

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

        if not conversacion._state.adding and not conversacion.mensajes.exists() and conversacion.titulo == "Consulta Normativa (Gestor)":
            conversacion.titulo = pregunta[:40] + ("..." if len(pregunta) > 40 else "")
            conversacion.save()

        msg_usuario, msg_sistema, citas = procesar_mensaje_chat(
            conversacion,
            pregunta,
            usuario,
        )

        return JsonResponse({
            'ok': True,
            'conversacion_id': str(conversacion.id),
            'conversacion_url': reverse('gestor_chat_conversacion', kwargs={'conversacion_id': conversacion.id}),
            'conversacion_titulo': conversacion.titulo,
            'mensajes_restantes': "Ilimitado",
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
        return JsonResponse({'error': 'La conversación no existe.'}, status=404)
    except Exception:
        logger.exception("Error interno al procesar un mensaje de gestor.")
        return JsonResponse(
            {'error': 'Ocurrió un error interno al procesar tu mensaje. Inténtalo de nuevo.'},
            status=500,
        )
