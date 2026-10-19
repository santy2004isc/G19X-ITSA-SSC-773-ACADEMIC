import json
from uuid import UUID
from django.http import Http404
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.db import transaction
from django.contrib import messages

from ...models import Usuario, Conversacion, Mensaje
from ...forms.usuario_forms import EditarPerfilUsuarioForm
from ...decorators import roles_requeridos
from ...services.chat_service import (
    MAX_LONGITUD_MENSAJE,
    buscar_contexto_normativo,
    clasificar_mensaje,
    generar_respuesta_ia_con_fallback,
    respuesta_sin_consulta_normativa,
)


@login_required
@roles_requeridos(Usuario.Rol.REGISTRADO)
def chat_interfaz_view(request, conversacion_id=None):
    """
    Interfaz de chat optimizada para Usuario Registrado.
    """
    usuario = request.user
    conversaciones = Conversacion.objects.filter(usuario=usuario).order_by('-fecha_creacion')
    
    conversacion_actual = None
    mensajes = []

    if conversacion_id:
        conversacion_actual = get_object_or_404(Conversacion, id=conversacion_id, usuario=usuario)
    elif conversaciones.exists():
        conversacion_actual = conversaciones.first()

    if conversacion_actual:
        mensajes = conversacion_actual.mensajes.all().order_by('fecha_envio')

    usuario.verificar_y_reiniciar_cuota_diaria()
    mensajes_restantes = max(0, usuario.cuota_diaria_mensajes - usuario.mensajes_usados_hoy)

    context = {
        'conversaciones': conversaciones,
        'conversacion_actual': conversacion_actual,
        'mensajes': mensajes,
        'mensajes_restantes': mensajes_restantes,
    }
    return render(request, 'usuario/chat.html', context)


@login_required
@roles_requeridos(Usuario.Rol.REGISTRADO)
@require_POST
def crear_nueva_conversacion_view(request):
    nueva_conv = Conversacion.objects.create(
        usuario=request.user,
        titulo="Consulta Normativa"
    )
    return redirect('chat_conversacion', conversacion_id=nueva_conv.id)


@login_required
@roles_requeridos(Usuario.Rol.REGISTRADO)
@require_POST
def eliminar_conversacion_view(request, conversacion_id):
    conv = get_object_or_404(Conversacion, id=conversacion_id, usuario=request.user)
    conv.delete()
    messages.success(request, "La conversación fue eliminada.")
    return redirect('chat_interfaz')


@login_required
@roles_requeridos(Usuario.Rol.REGISTRADO)
@require_POST
def enviar_mensaje_api_view(request):
    """
    Endpoint AJAX con control estricto de cuota diaria.
    """
    usuario = request.user

    usuario.verificar_y_reiniciar_cuota_diaria()

    # 1. Validación estricta de Cuota Diaria
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

    conversacion_id = data.get('conversacion_id')
    if conversacion_id is not None and not isinstance(conversacion_id, str):
        return JsonResponse({'error': 'El identificador de conversación no es válido.'}, status=400)
    if conversacion_id:
        try:
            conversacion_id = UUID(conversacion_id)
        except ValueError:
            return JsonResponse({'error': 'El identificador de conversación no es válido.'}, status=400)

    try:
        # 2. Asignar o crear conversación
        if conversacion_id:
            conversacion = get_object_or_404(Conversacion, id=conversacion_id, usuario=usuario)
        else:
            titulo_auto = pregunta[:40] + ("..." if len(pregunta) > 40 else "")
            conversacion = Conversacion.objects.create(usuario=usuario, titulo=titulo_auto)

        if not conversacion.mensajes.exists() and conversacion.titulo == "Consulta Normativa":
            conversacion.titulo = pregunta[:40] + ("..." if len(pregunta) > 40 else "")
            conversacion.save()

        with transaction.atomic():
            # 3. Guardar pregunta del usuario
            msg_usuario = Mensaje.objects.create(
                conversacion=conversacion,
                emisor=Mensaje.Emisor.USUARIO,
                contenido_texto=pregunta
            )

            # Los mensajes sociales y ajenos al ámbito jurídico no activan RAG ni IA.
            tipo_mensaje = clasificar_mensaje(pregunta)
            if tipo_mensaje == 'juridico':
                contexto, citas = buscar_contexto_normativo(pregunta)
                respuesta_texto, proveedor_usado, conmutado = generar_respuesta_ia_con_fallback(
                    pregunta_usuario=pregunta,
                    contexto_normativo=contexto,
                    usuario=usuario
                )
            else:
                respuesta_texto = respuesta_sin_consulta_normativa(tipo_mensaje)
                proveedor_usado = None
                conmutado = False
                citas = []

            # 5. Guardar respuesta del sistema
            msg_sistema = Mensaje.objects.create(
                conversacion=conversacion,
                emisor=Mensaje.Emisor.SISTEMA,
                contenido_texto=respuesta_texto,
                proveedor_ia=proveedor_usado,
                conmutado_fallback=conmutado,
                citas=citas
            )

            # 6. Incrementar consumo de cuota diaria
            usuario.mensajes_usados_hoy += 1
            usuario.save(update_fields=['mensajes_usados_hoy'])

        mensajes_restantes = max(0, usuario.cuota_diaria_mensajes - usuario.mensajes_usados_hoy)

        return JsonResponse({
            'ok': True,
            'conversacion_id': str(conversacion.id),
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
        return JsonResponse({'error': 'La conversación no existe.'}, status=404)
    except Exception as e:
        return JsonResponse({'error': f'Error interno: {str(e)}'}, status=500)


@login_required
@roles_requeridos(Usuario.Rol.REGISTRADO)
def perfil_usuario_view(request):
    user = request.user
    if request.method == 'POST':
        form = EditarPerfilUsuarioForm(request.POST, instance=user, user=user)
        if form.is_valid():
            form.save()
            messages.success(request, "Tu perfil ha sido actualizado.")
            return redirect('usuario_perfil')
    else:
        form = EditarPerfilUsuarioForm(instance=user, user=user)

    return render(request, 'usuario/perfil.html', {'form': form})