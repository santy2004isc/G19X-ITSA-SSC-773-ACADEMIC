class NormalizarCorreoElectronicoMixin:
    def clean_correo_electronico(self):
        correo = self.cleaned_data.get('correo_electronico')
        return correo.strip().lower() if correo else correo
