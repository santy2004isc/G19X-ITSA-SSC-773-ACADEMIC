from django import forms
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from ..models import Usuario, LeyFederal

INPUT_STYLE = 'w-full px-4 py-2.5 bg-white text-slate-800 border border-slate-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500 focus:outline-none shadow-sm transition duration-150'
SOLO_LETRAS_VALIDATOR = RegexValidator(
    regex=r'^[a-zA-ZáéíóúÁÉÍÓÚñÑ\s]+$',
    message='Este campo solo debe contener letras y espacios.'
)


class CargarDocumentoForm(forms.ModelForm):
    """
    Formulario para carga de nuevas Leyes (fecha_ultima_reforma opcional).
    """
    archivo_pdf = forms.FileField(
        label="Archivo Ley Federal (PDF)",
        widget=forms.FileInput(attrs={
            'class': 'absolute inset-0 h-full w-full cursor-pointer opacity-0',
            'accept': '.pdf',
            'id': 'input_archivo_pdf',
        }),
        help_text="PDF legible de una ley, código u otro documento normativo. Se analizará la primera página para sugerir los datos."
    )
    fecha_publicacion = forms.DateField(
        label="Fecha de Publicación",
        widget=forms.DateInput(format='%Y-%m-%d', attrs={'class': INPUT_STYLE, 'type': 'date'})
    )
    fecha_ultima_reforma = forms.DateField(
        required=False,
        widget=forms.DateInput(format='%Y-%m-%d', attrs={'class': INPUT_STYLE, 'type': 'date'}),
        label="Fecha de Última Reforma (Opcional)"
    )

    class Meta:
        model = LeyFederal
        fields = ['nombre', 'siglas', 'fecha_publicacion', 'fecha_ultima_reforma', 'estado']
        widgets = {
            'nombre': forms.TextInput(attrs={'class': INPUT_STYLE, 'placeholder': 'Ej. Ley Federal del Trabajo'}),
            'siglas': forms.TextInput(attrs={'class': INPUT_STYLE, 'placeholder': 'Ej. LFT'}),
            'estado': forms.Select(attrs={'class': INPUT_STYLE}),
        }

    def clean_archivo_pdf(self):
        archivo = self.cleaned_data.get('archivo_pdf')
        if archivo:
            if not archivo.name.lower().endswith('.pdf'):
                raise ValidationError("El archivo seleccionado debe ser obligatoriamente en formato .pdf")
            if archivo.size > 50 * 1024 * 1024:
                raise ValidationError("El tamaño del archivo PDF no puede exceder los 50MB.")
        return archivo


class EditarLeyFederalForm(forms.ModelForm):
    """
    Formulario para actualizar metadatos y opcionalmente re-vectorizar la Ley Federal.
    """
    archivo_pdf = forms.FileField(
        label="Nuevo Archivo PDF (Opcional)",
        required=False,
        widget=forms.FileInput(attrs={
            'class': 'absolute inset-0 h-full w-full cursor-pointer opacity-0',
            'accept': '.pdf',
            'id': 'input_archivo_pdf',
        }),
        help_text="Cargue un PDF nuevo para actualizar el contenido. Se analizará la primera página para sugerir los metadatos."
    )
    # format='%Y-%m-%d' corrige el problema de visualización en <input type="date">
    fecha_ultima_reforma = forms.DateField(
        required=False,
        widget=forms.DateInput(format='%Y-%m-%d', attrs={'class': INPUT_STYLE, 'type': 'date', 'id': 'input_fecha_reforma'}),
        label="Fecha de Última Reforma"
    )
    fecha_publicacion = forms.DateField(
        widget=forms.DateInput(format='%Y-%m-%d', attrs={'class': INPUT_STYLE, 'type': 'date', 'id': 'input_fecha_publicacion'}),
        label="Fecha de Publicación"
    )

    class Meta:
        model = LeyFederal
        fields = ['nombre', 'siglas', 'fecha_publicacion', 'fecha_ultima_reforma', 'estado']
        widgets = {
            'nombre': forms.TextInput(attrs={'class': INPUT_STYLE}),
            'siglas': forms.TextInput(attrs={'class': INPUT_STYLE}),
            'estado': forms.Select(attrs={'class': INPUT_STYLE}),
        }

    def clean_archivo_pdf(self):
        archivo = self.cleaned_data.get('archivo_pdf')
        if archivo:
            if not archivo.name.lower().endswith('.pdf'):
                raise ValidationError("El archivo seleccionado debe ser obligatoriamente en formato .pdf")
            if archivo.size > 50 * 1024 * 1024:
                raise ValidationError("El tamaño del archivo PDF no puede exceder los 50MB.")
        return archivo

    def clean(self):
        cleaned_data = super().clean()
        archivo_pdf = cleaned_data.get('archivo_pdf')
        fecha_reforma = cleaned_data.get('fecha_ultima_reforma')

        # Validación condicional: Obligatorio si hay archivo seleccionado
        if archivo_pdf and not fecha_reforma:
            self.add_error(
                'fecha_ultima_reforma', 
                'Si adjunta un nuevo archivo PDF para actualizar la ley, la Fecha de Última Reforma es obligatoria.'
            )

        return cleaned_data


class EditarPerfilGestorForm(forms.ModelForm):
    """
    Formulario de perfil para el Gestor.
    """
    nombre = forms.CharField(label="Nombre(s)", validators=[SOLO_LETRAS_VALIDATOR], widget=forms.TextInput(attrs={'class': INPUT_STYLE}))
    apellido = forms.CharField(label="Apellidos", validators=[SOLO_LETRAS_VALIDATOR], widget=forms.TextInput(attrs={'class': INPUT_STYLE}))
    
    password_actual = forms.CharField(
        label="Contraseña Actual (Obligatoria para confirmar cambios)",
        required=True,
        widget=forms.PasswordInput(attrs={'class': INPUT_STYLE, 'placeholder': '••••••••', 'id': 'input_gestor_pass_actual'})
    )
    password_nueva = forms.CharField(
        label="Nueva Contraseña (Opcional)",
        required=False,
        min_length=8,
        widget=forms.PasswordInput(attrs={'class': INPUT_STYLE, 'placeholder': 'Dejar en blanco para no modificar', 'id': 'input_gestor_pass_nueva'}),
        error_messages={'min_length': 'La nueva contraseña debe tener al menos 8 caracteres.'}
    )
    password_confirmacion = forms.CharField(
        label="Confirmar Nueva Contraseña",
        required=False,
        widget=forms.PasswordInput(attrs={'class': INPUT_STYLE, 'placeholder': 'Confirmar únicamente si cambió la contraseña', 'id': 'input_gestor_pass_confirm'})
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
            raise ValidationError("La contraseña actual proporcionada no es correcta.")
        return actual

    def clean(self):
        cleaned_data = super().clean()
        nueva = cleaned_data.get("password_nueva")
        confirm = cleaned_data.get("password_confirmacion")

        if nueva or confirm:
            if nueva != confirm:
                raise ValidationError("Las nuevas contraseñas no coinciden.")
        return cleaned_data

    def save(self, commit=True):
        user = super().save(commit=False)
        nueva = self.cleaned_data.get("password_nueva")
        if nueva:
            user.set_password(nueva)
        if commit:
            user.save()
        return user