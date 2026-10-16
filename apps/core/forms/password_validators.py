from django.core.validators import RegexValidator


PASSWORD_POLICY_PATTERN = (
    r'^(?=.*[A-Za-zÁÉÍÓÚÜÑáéíóúüñ])(?=.*[0-9])'
    r'(?=.*[^A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9]).{8,}$'
)
PASSWORD_POLICY_MESSAGE = (
    'La contraseña debe tener al menos 8 caracteres, una letra, un número '
    'y un carácter especial.'
)

PASSWORD_POLICY_VALIDATOR = RegexValidator(
    regex=PASSWORD_POLICY_PATTERN,
    message=PASSWORD_POLICY_MESSAGE,
)
