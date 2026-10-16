from django import forms
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from ..models import Usuario
from .password_validators import (
    PASSWORD_POLICY_MESSAGE,
    PASSWORD_POLICY_PATTERN,
    PASSWORD_POLICY_VALIDATOR,
)

INPUT_STYLE = 'w-full px-4 py-2.5 bg-white text-slate-800 border border-slate-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500 focus:outline-none shadow-sm transition duration-150'
SOLO_LETRAS_VALIDATOR = RegexValidator(
    regex=r'^[a-zA-ZáéíóúÁÉÍÓÚñÑ\s]+$',
    message='Este campo solo debe contener letras y espacios.'
)


class CrearUsuarioAdminForm(forms.ModelForm):
    """
    Formulario para la creación de usuarios con confirmación de contraseña.
    """
    nombre = forms.CharField(label="Nombre(s)", validators=[SOLO_LETRAS_VALIDATOR], widget=forms.TextInput(attrs={'class': INPUT_STYLE}))
    apellido = forms.CharField(label="Apellidos", validators=[SOLO_LETRAS_VALIDATOR], widget=forms.TextInput(attrs={'class': INPUT_STYLE}))
    password = forms.CharField(
        label="Contraseña",
        min_length=8,
        validators=[PASSWORD_POLICY_VALIDATOR],
        widget=forms.PasswordInput(attrs={
            'class': INPUT_STYLE, 'placeholder': 'Mínimo 8 caracteres',
            'id': 'input_create_password', 'data-password-policy': 'true',
            'pattern': PASSWORD_POLICY_PATTERN[1:-1], 'title': PASSWORD_POLICY_MESSAGE,
        }),
        error_messages={'min_length': 'La contraseña debe tener al menos 8 caracteres.'}
    )
    password_confirmacion = forms.CharField(
        label="Confirmar Contraseña",
        min_length=8,
        widget=forms.PasswordInput(attrs={
            'class': INPUT_STYLE, 'placeholder': '••••••••',
            'id': 'input_create_password_confirm',
            'data-password-match': 'input_create_password',
        })
    )

    class Meta:
        model = Usuario
        fields = ['nombre', 'apellido', 'correo_electronico', 'rol', 'cuota_diaria_mensajes', 'activo']
        widgets = {
            'correo_electronico': forms.EmailInput(attrs={'class': INPUT_STYLE}),
            'rol': forms.Select(attrs={'class': INPUT_STYLE}),
            'cuota_diaria_mensajes': forms.NumberInput(attrs={'class': INPUT_STYLE}),
            'activo': forms.CheckboxInput(attrs={'class': 'w-5 h-5 text-blue-600 border-slate-300 rounded focus:ring-blue-500'}),
        }

    def clean(self):
        cleaned_data = super().clean()
        pass1 = cleaned_data.get("password")
        pass2 = cleaned_data.get("password_confirmacion")
        if pass1 and pass2 and pass1 != pass2:
            raise ValidationError("Las contraseñas no coinciden.")
        return cleaned_data

    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data["password"])
        if commit:
            user.save()
        return user

class EditarUsuarioAdminForm(forms.ModelForm):
    """
    Formulario para editar información de usuario y/o cambiar su contraseña en la misma vista.
    """
    nombre = forms.CharField(label="Nombre(s)", validators=[SOLO_LETRAS_VALIDATOR], widget=forms.TextInput(attrs={'class': INPUT_STYLE}))
    apellido = forms.CharField(label="Apellidos", validators=[SOLO_LETRAS_VALIDATOR], widget=forms.TextInput(attrs={'class': INPUT_STYLE}))
    
    # Campos opcionales para la contraseña
    password_nueva = forms.CharField(
        label="Nueva Contraseña (Opcional)",
        required=False,
        min_length=8,
        validators=[PASSWORD_POLICY_VALIDATOR],
        widget=forms.PasswordInput(attrs={
            'class': INPUT_STYLE, 'placeholder': 'Dejar en blanco para conservar la actual',
            'id': 'input_edit_password_nueva', 'data-password-policy': 'true',
            'pattern': PASSWORD_POLICY_PATTERN[1:-1], 'title': PASSWORD_POLICY_MESSAGE,
        }),
        error_messages={'min_length': 'La nueva contraseña debe tener al menos 8 caracteres.'}
    )
    password_confirmacion = forms.CharField(
        label="Confirmar Nueva Contraseña",
        required=False,
        widget=forms.PasswordInput(attrs={
            'class': INPUT_STYLE, 'placeholder': 'Confirmar únicamente si se cambió',
            'id': 'input_edit_password_confirm',
            'data-password-match': 'input_edit_password_nueva',
        })
    )

    class Meta:
        model = Usuario
        fields = ['nombre', 'apellido', 'correo_electronico', 'rol', 'cuota_diaria_mensajes', 'activo']
        widgets = {
            'correo_electronico': forms.EmailInput(attrs={'class': INPUT_STYLE}),
            'rol': forms.Select(attrs={'class': INPUT_STYLE}),
            'cuota_diaria_mensajes': forms.NumberInput(attrs={'class': INPUT_STYLE}),
            'activo': forms.CheckboxInput(attrs={'class': 'w-5 h-5 text-blue-600 border-slate-300 rounded focus:ring-blue-500'}),
        }

    def clean(self):
        cleaned_data = super().clean()
        nueva = cleaned_data.get("password_nueva")
        confirm = cleaned_data.get("password_confirmacion")

        if nueva or confirm:
            if nueva != confirm:
                raise ValidationError("Las contraseñas no coinciden.")
        return cleaned_data

    def save(self, commit=True):
        user = super().save(commit=False)
        nueva = self.cleaned_data.get("password_nueva")
        if nueva:
            user.set_password(nueva)
        if commit:
            user.save()
        return user