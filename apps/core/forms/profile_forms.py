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


class EditarPerfilForm(forms.ModelForm):
    """
    Edición de datos personales y actualización opcional de contraseña en un único formulario.
    """
    nombre = forms.CharField(label="Nombre(s)", validators=[SOLO_LETRAS_VALIDATOR], widget=forms.TextInput(attrs={'class': INPUT_STYLE}))
    apellido = forms.CharField(label="Apellidos", validators=[SOLO_LETRAS_VALIDATOR], widget=forms.TextInput(attrs={'class': INPUT_STYLE}))
    
    password_actual = forms.CharField(
        label="Contraseña Actual (Requerida para guardar cambios)",
        required=True,
        widget=forms.PasswordInput(attrs={'class': INPUT_STYLE, 'placeholder': '••••••••', 'id': 'input_perfil_pass_actual'})
    )
    password_nueva = forms.CharField(
        label="Nueva Contraseña (Opcional)",
        required=False,
        min_length=8,
        validators=[PASSWORD_POLICY_VALIDATOR],
        widget=forms.PasswordInput(attrs={
            'class': INPUT_STYLE, 'placeholder': 'Dejar en blanco para conservar la actual',
            'id': 'input_perfil_pass_nueva', 'data-password-policy': 'true',
            'pattern': PASSWORD_POLICY_PATTERN[1:-1], 'title': PASSWORD_POLICY_MESSAGE,
        }),
        error_messages={'min_length': 'La nueva contraseña debe tener al menos 8 caracteres.'}
    )
    password_confirmacion = forms.CharField(
        label="Confirmar Nueva Contraseña",
        required=False,
        widget=forms.PasswordInput(attrs={
            'class': INPUT_STYLE, 'placeholder': 'Confirmar únicamente si cambió la contraseña',
            'id': 'input_perfil_pass_confirm',
            'data-password-match': 'input_perfil_pass_nueva',
        })
    )

    class Meta:
        model = Usuario
        fields = ['nombre', 'apellido', 'correo_electronico']
        widgets = {
            'correo_electronico': forms.EmailInput(attrs={'class': INPUT_STYLE}),
        }

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)

    def clean_password_actual(self):
        actual = self.cleaned_data.get('password_actual')
        if self.user and not self.user.check_password(actual):
            raise ValidationError("La contraseña actual es incorrecta.")
        return actual

    def clean(self):
        cleaned_data = super().clean()
        nueva = cleaned_data.get("password_nueva")
        confirm = cleaned_data.get("password_confirmacion")

        if nueva or confirm:
            if nueva != confirm:
                raise ValidationError("Las contraseñas nuevas no coinciden.")
        return cleaned_data

    def save(self, commit=True):
        user = super().save(commit=False)
        nueva = self.cleaned_data.get("password_nueva")
        if nueva:
            user.set_password(nueva)
        if commit:
            user.save()
        return user