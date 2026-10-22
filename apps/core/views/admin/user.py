from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db.models import Count, Q
from functools import wraps
from ...models import Usuario
from ...forms.admin_forms import CrearUsuarioAdminForm, EditarUsuarioAdminForm


def admin_required(view_func):
    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        if not request.user.is_authenticated or request.user.rol != Usuario.Rol.ADMINISTRADOR:
            messages.error(request, "Acceso restringido. Se requieren permisos de Administrador.")
            return redirect('login')
        return view_func(request, *args, **kwargs)
    return _wrapped_view


@login_required
@admin_required
def admin_dashboard_view(request):
    query = request.GET.get('q', '').strip()
    ordenes_permitidos = {
        'nombre': ('nombre', 'apellido'),
        'correo': ('correo_electronico',),
        'rol': ('rol',),
        'estado': ('activo',),
    }
    orden = request.GET.get('orden', '')
    direccion = request.GET.get('direccion', 'asc')
    campo_orden = ordenes_permitidos.get(orden)
    if direccion not in ('asc', 'desc'):
        direccion = 'asc'

    usuarios = Usuario.objects.exclude(id=request.user.id).filter(is_superuser=False)
    if query:
        usuarios = usuarios.filter(
            Q(nombre__icontains=query) |
            Q(apellido__icontains=query) |
            Q(correo_electronico__icontains=query)
        )
    if campo_orden:
        prefijo_orden = '' if direccion == 'asc' else '-'
        usuarios = usuarios.order_by(
            *(f'{prefijo_orden}{campo}' for campo in campo_orden),
            'pk',
        )
    else:
        orden = ''
        usuarios = usuarios.order_by('-fecha_registro', 'pk')

    conteos = Usuario.objects.aggregate(
        total=Count('pk'),
        gestores=Count('pk', filter=Q(rol=Usuario.Rol.GESTOR)),
        registrados=Count('pk', filter=Q(rol=Usuario.Rol.REGISTRADO)),
    )
    
    context = {
        'total_usuarios': conteos['total'],
        'total_gestores': conteos['gestores'],
        'total_registrados': conteos['registrados'],
        'usuarios': usuarios,
        'query': query,
        'orden_actual': orden,
        'direccion_actual': direccion,
    }
    return render(request, 'admin/dashboard.html', context)


@login_required
@admin_required
def crear_usuario_view(request):
    if request.method == 'POST':
        form = CrearUsuarioAdminForm(request.POST)
        if form.is_valid():
            usuario = form.save()
            messages.success(request, f"Usuario creado exitosamente.")
            return redirect('admin_dashboard')
    else:
        form = CrearUsuarioAdminForm()

    return render(request, 'admin/usuario_form.html', {'form': form, 'es_edicion': False})


@login_required
@admin_required
def editar_usuario_view(request, user_id):
    usuario = get_object_or_404(Usuario, id=user_id, is_superuser=False)
    if usuario == request.user:
        messages.error(request, "Utiliza la opción 'Mi Perfil' para editar tus datos.")
        return redirect('admin_dashboard')

    if request.method == 'POST':
        form = EditarUsuarioAdminForm(request.POST, instance=usuario)
        if form.is_valid():
            form.save()
            messages.success(request, f"{usuario.nombre} actualizado correctamente.")
            return redirect('admin_dashboard')
    else:
        form = EditarUsuarioAdminForm(instance=usuario)

    return render(request, 'admin/usuario_form.html', {
        'form': form, 
        'usuario_target': usuario, 
        'es_edicion': True
    })


@login_required
@admin_required
def cambiar_estado_usuario_view(request, user_id):
    usuario = get_object_or_404(Usuario, id=user_id, is_superuser=False)
    if usuario == request.user:
        messages.error(request, "No puedes desactivar tu propia cuenta.")
    else:
        usuario.activo = not usuario.activo
        usuario.save()
        estado = "activada" if usuario.activo else "desactivada"
        messages.success(request, f"La cuenta de {usuario.correo_electronico} ha sido {estado}.")
    return redirect('admin_dashboard')


@login_required
@admin_required
def eliminar_usuario_view(request, user_id):
    usuario = get_object_or_404(Usuario, id=user_id, is_superuser=False)
    if usuario == request.user:
        messages.error(request, "No puedes eliminar tu propia cuenta.")
    else:
        correo = usuario.correo_electronico
        usuario.delete()
        messages.success(request, f"La cuenta {correo} ha sido eliminada.")
    return redirect('admin_dashboard')