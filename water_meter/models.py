from django.db import models
from django.contrib.auth.models import User


class Profile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="profile")
    phone = models.CharField(max_length=20, blank=True, default="")

    def __str__(self):
        return self.user.username


class WaterMeter(models.Model):
    device_eui = models.CharField(max_length=16, unique=True)
    location = models.CharField(max_length=255, blank=True, default="")
    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name="meters")
    balance = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.location} ({self.device_eui})"


class Payment(models.Model):
    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("approved", "Approved"),
        ("failed", "Failed"),
        ("declined", "Declined"),
        ("canceled", "Canceled"),
    ]
    meter = models.ForeignKey(
        WaterMeter, on_delete=models.CASCADE, related_name="payments"
    )
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="payments")
    reference_id = models.CharField(max_length=50, unique=True)
    waafipay_transaction_id = models.CharField(max_length=50, blank=True, default="")
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=3, default="USD")
    phone = models.CharField(max_length=20)
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default="pending"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.reference_id} — {self.amount} {self.currency} ({self.status})"


class MeterTelemetry(models.Model):
    meter = models.ForeignKey(WaterMeter, on_delete=models.CASCADE, related_name="telemetry")

    forward_instantaneous_flow = models.FloatField(help_text="M3/h")
    forward_cumulative_flow = models.FloatField(help_text="M3")
    reverse_instantaneous_flow = models.FloatField(help_text="M3/h")
    reverse_cumulative_flow = models.FloatField(help_text="M3")

    voltage_rtu = models.FloatField(help_text="Volts")
    voltage_meter = models.FloatField(help_text="Volts")

    memory_alarm = models.BooleanField(default=False)
    flow_meter_alarm = models.BooleanField(default=False)
    low_battery = models.BooleanField(default=False)
    valve_open = models.BooleanField(default=False)
    magnetic_attack = models.BooleanField(default=False)
    leakage = models.BooleanField(default=False)
    pipe_burst = models.BooleanField(default=False)
    validity_invalid = models.BooleanField(default=False)

    meter_time = models.DateTimeField(help_text="Hardware timestamp (Tp)")
    server_received_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-meter_time"]
        verbose_name_plural = "meter telemetry"

    def __str__(self):
        return f"{self.meter.device_eui} @ {self.meter_time}"
