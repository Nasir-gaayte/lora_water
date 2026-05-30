from django import forms
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from .models import WaterMeter


class WaterMeterForm(forms.ModelForm):
    class Meta:
        model = WaterMeter
        fields = ["device_eui", "location"]
        widgets = {
            "device_eui": forms.TextInput(attrs={"placeholder": "e.g. A1A2A3A4A5A6A7A8"}),
            "location": forms.TextInput(attrs={"placeholder": "e.g. Pump Station 3"}),
        }
        labels = {
            "device_eui": "Device EUI",
            "location": "Location",
        }


class UserUpdateForm(forms.ModelForm):
    phone = forms.CharField(max_length=20, required=False)

    class Meta:
        model = User
        fields = ["username", "email"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and hasattr(self.instance, "profile"):
            self.fields["phone"].initial = self.instance.profile.phone


class CustomerRegistrationForm(forms.Form):
    username = forms.CharField(
        max_length=150,
        widget=forms.TextInput(attrs={"placeholder": "Choose a username"}),
    )
    email = forms.EmailField(
        widget=forms.EmailInput(attrs={"placeholder": "your@email.com"}),
    )
    phone = forms.CharField(
        max_length=20,
        required=False,
        widget=forms.TextInput(attrs={"placeholder": "e.g. +201234567890"}),
    )
    password = forms.CharField(
        widget=forms.PasswordInput(attrs={"placeholder": "Password"}),
        validators=[validate_password],
    )
    password2 = forms.CharField(
        label="Confirm password",
        widget=forms.PasswordInput(attrs={"placeholder": "Repeat password"}),
    )
    location = forms.CharField(
        max_length=255,
        required=False,
        widget=forms.TextInput(attrs={"placeholder": "e.g. Pump Station 3, Building A"}),
        label="Location",
    )
    device_eui = forms.CharField(
        max_length=16,
        min_length=16,
        widget=forms.TextInput(attrs={
            "placeholder": "Scan or enter 16-char EUI",
            "id": "id_device_eui",
        }),
        label="Device EUI (barcode)",
    )

    def clean_username(self):
        val = self.cleaned_data["username"]
        if User.objects.filter(username=val).exists():
            raise forms.ValidationError("Username already taken.")
        return val

    def clean_email(self):
        val = self.cleaned_data["email"]
        if User.objects.filter(email=val).exists():
            raise forms.ValidationError("Email already registered.")
        return val

    def clean_device_eui(self):
        val = self.cleaned_data["device_eui"].strip().upper()
        if WaterMeter.objects.filter(device_eui=val).exists():
            raise forms.ValidationError("This meter barcode is already registered.")
        return val

    def clean(self):
        cleaned = super().clean()
        p1 = cleaned.get("password")
        p2 = cleaned.get("password2")
        if p1 and p2 and p1 != p2:
            raise forms.ValidationError("Passwords do not match.")
        return cleaned
