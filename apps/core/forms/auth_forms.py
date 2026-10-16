from django import forms
from django.contrib.auth import authenticate
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from ..models import Usuario
from .password_validators import (
    PASSWORD_POLICY_MESSAGE,
    PASSWORD_POLICY_PATTERN,
    PASSWORD_POLICY_VALIDATOR,
)

INPUT_STYLE = 'w-full px-4 py-2.5 bg-white text-slate-800 border border-slate-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500 focus:outline-none shadow-sm transition duration-150'

# Validador de solo letras, espacios y caracteres en español
SOLO_LETRAS_VALIDATOR = RegexValidator(
    regex=r'^[a-zA-ZáéíóúÁÉÍÓÚñÑ\s]+$',
    message='Este campo solo debe contener letras y espacios.'
)


class LoginForm(forms.Form):
    """
    Formulario de Inicio de Sesión.
    """
    correo_electronico = forms.EmailField(
        label="Correo Electrónico",
        widget=forms.EmailInput(attrs={
            'class': INPUT_STYLE,
            'placeholder': 'correo@ejemplo.com',
            'required': True,
        })
    )
    password = forms.CharField(
        label="Contraseña",
        min_length=8,
        widget=forms.PasswordInput(attrs={
            'class': INPUT_STYLE,
            'placeholder': '••••••••',
            'required': True,
            'id': 'input_password_login',
        }),
        error_messages={
            'min_length': 'La contraseña debe tener al menos 8 caracteres.'
        }
    )

    def clean(self):
        cleaned_data = super().clean()
        correo = cleaned_data.get('correo_electronico')
        password = cleaned_data.get('password')

        if correo and password:
            user = authenticate(username=correo, password=password)
            if user is not None:
                if not user.is_active:
                    raise ValidationError("Esta cuenta se encuentra inactiva.")
                cleaned_data['user'] = user
            else:
                # Si authenticate retornó None, verificar si el usuario existe y su contraseña coincide
                # pero su cuenta está inactiva (is_active=False)
                try:
                    user_existente = Usuario.objects.get(correo_electronico__iexact=correo)
                    if user_existente.check_password(password) and not user_existente.is_active:
                        raise ValidationError("Esta cuenta se encuentra inactiva.")
                except Usuario.DoesNotExist:
                    pass

                raise ValidationError("Correo electrónico o contraseña incorrectos.")

        return cleaned_data


class RegistroForm(forms.ModelForm):
    """
    Formulario de Registro Público con validaciones estrictas.
    """
    nombre = forms.CharField(
        label="Nombre(s)",
        validators=[SOLO_LETRAS_VALIDATOR],
        widget=forms.TextInput(attrs={'class': INPUT_STYLE})
    )
    apellido = forms.CharField(
        label="Apellidos",
        validators=[SOLO_LETRAS_VALIDATOR],
        widget=forms.TextInput(attrs={'class': INPUT_STYLE})
    )
    password = forms.CharField(
        label="Contraseña",
        min_length=8,
        validators=[PASSWORD_POLICY_VALIDATOR],
        widget=forms.PasswordInput(attrs={
            'class': INPUT_STYLE,
            'placeholder': 'Mínimo 8 caracteres',
            'required': True,
            'id': 'input_password_reg',
            'data-password-policy': 'true',
            'pattern': PASSWORD_POLICY_PATTERN[1:-1],
            'title': PASSWORD_POLICY_MESSAGE,
        }),
        error_messages={
            'min_length': 'La contraseña debe tener al menos 8 caracteres.'
        }
    )
    password_confirmacion = forms.CharField(
        label="Confirmar Contraseña",
        min_length=8,
        widget=forms.PasswordInput(attrs={
            'class': INPUT_STYLE,
            'placeholder': '••••••••',
            'required': True,
            'id': 'input_password_confirm',
            'data-password-match': 'input_password_reg',
        })
    )

    class Meta:
        model = Usuario
        fields = ['nombre', 'apellido', 'correo_electronico']
        widgets = {
            'correo_electronico': forms.EmailInput(attrs={
                'class': INPUT_STYLE,
                'placeholder': 'correo@ejemplo.com'
            }),
        }

    def clean_password_confirmacion(self):
        password = self.cleaned_data.get("password")
        password_confirmacion = self.cleaned_data.get("password_confirmacion")
        if password and password_confirmacion and password != password_confirmacion:
            raise ValidationError("Las contraseñas no coinciden.")
        return password_confirmacion

    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data["password"])
        user.rol = Usuario.Rol.REGISTRADO
        if commit:
            user.save()
        return user