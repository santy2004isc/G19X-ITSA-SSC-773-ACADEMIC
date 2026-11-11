from django import forms
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.forms import ReadOnlyPasswordHashField

from .forms.password_validators import PASSWORD_POLICY_VALIDATOR
from .forms.mixins import NormalizarCorreoElectronicoMixin
from .models import (
    Conversacion,
    FragmentoNormativo,
    LeyFederal,
    Mensaje,
    RegistroFalloAPI,
    Usuario,
)


class UsuarioAdminCreationForm(NormalizarCorreoElectronicoMixin, forms.ModelForm):
    password1 = forms.CharField(
        label='Contraseña',
        min_length=8,
        validators=[PASSWORD_POLICY_VALIDATOR],
        widget=forms.PasswordInput,
    )
    password2 = forms.CharField(
        label='Confirmar contraseña',
        min_length=8,
        widget=forms.PasswordInput,
    )

    class Meta:
        model = Usuario
        fields = (
            'correo_electronico',
            'nombre',
            'apellido',
            'rol',
            'activo',
            'is_staff',
            'is_superuser',
        )

    def clean_password2(self):
        password1 = self.cleaned_data.get('password1')
        password2 = self.cleaned_data.get('password2')
        if password1 and password2 and password1 != password2:
            raise forms.ValidationError('Las contraseñas no coinciden.')
        return password2

    def save(self, commit=True):
        usuario = super().save(commit=False)
        usuario.set_password(self.cleaned_data['password1'])
        if commit:
            usuario.save()
            self.save_m2m()
        return usuario


class UsuarioAdminChangeForm(NormalizarCorreoElectronicoMixin, forms.ModelForm):
    password = ReadOnlyPasswordHashField(label='Contraseña')

    class Meta:
        model = Usuario
        fields = '__all__'

    def clean_password(self):
        return self.initial['password']


@admin.register(Usuario)
class UsuarioAdmin(UserAdmin):
    form = UsuarioAdminChangeForm
    add_form = UsuarioAdminCreationForm
    ordering = ('correo_electronico',)
    list_display = ('correo_electronico', 'nombre', 'apellido', 'rol', 'activo', 'is_staff')
    list_filter = ('rol', 'activo', 'is_staff', 'is_superuser')
    search_fields = ('correo_electronico', 'nombre', 'apellido')
    filter_horizontal = ('groups', 'user_permissions')
    fieldsets = (
        (None, {'fields': ('correo_electronico', 'password')}),
        ('Información personal', {'fields': ('nombre', 'apellido', 'rol')}),
        ('Estado y permisos', {'fields': ('activo', 'is_staff', 'is_superuser', 'groups', 'user_permissions')}),
        ('Fechas', {'fields': ('last_login', 'fecha_registro')}),
    )
    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': (
                'correo_electronico',
                'nombre',
                'apellido',
                'rol',
                'activo',
                'is_staff',
                'is_superuser',
                'password1',
                'password2',
            ),
        }),
    )
    readonly_fields = ('last_login', 'fecha_registro')


@admin.register(LeyFederal)
class LeyFederalAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'siglas', 'estado', 'estado_procesamiento', 'fecha_creacion')
    list_filter = ('estado', 'estado_procesamiento')
    search_fields = ('nombre', 'siglas')
    readonly_fields = ('hash_documento', 'hash_contenido', 'error_procesamiento')


@admin.register(FragmentoNormativo)
class FragmentoNormativoAdmin(admin.ModelAdmin):
    list_display = ('id', 'ley', 'articulo', 'fecha_creacion')
    list_filter = ('ley',)
    search_fields = ('ley__nombre', 'ley__siglas', 'articulo', 'contenido_fragmento')
    list_select_related = ('ley',)
    readonly_fields = ('vector_embedding', 'hash_fragmento', 'fecha_creacion')


@admin.register(Conversacion)
class ConversacionAdmin(admin.ModelAdmin):
    list_display = ('titulo', 'usuario', 'fecha_creacion')
    search_fields = ('titulo', 'usuario__correo_electronico')
    list_select_related = ('usuario',)


@admin.register(Mensaje)
class MensajeAdmin(admin.ModelAdmin):
    list_display = ('id', 'conversacion', 'emisor', 'proveedor_ia', 'fecha_envio')
    list_filter = ('emisor', 'proveedor_ia', 'conmutado_fallback')
    search_fields = ('contenido_texto', 'conversacion__titulo', 'conversacion__usuario__correo_electronico')
    list_select_related = ('conversacion', 'conversacion__usuario')


@admin.register(RegistroFalloAPI)
class RegistroFalloAPIAdmin(admin.ModelAdmin):
    list_display = ('fecha_evento', 'usuario', 'proveedor_solicitado', 'codigo_error', 'conmutado_a', 'exitoso')
    list_filter = ('proveedor_solicitado', 'conmutado_a', 'exitoso')
    search_fields = ('usuario__correo_electronico', 'codigo_error')
    list_select_related = ('usuario', 'mensaje')
