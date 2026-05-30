from django.contrib import admin
from .models import WaterMeter, MeterTelemetry


@admin.register(WaterMeter)
class WaterMeterAdmin(admin.ModelAdmin):
    list_display = ("device_eui", "location", "owner", "created_at")
    search_fields = ("device_eui", "location")
    list_filter = ("created_at",)


@admin.register(MeterTelemetry)
class MeterTelemetryAdmin(admin.ModelAdmin):
    list_display = (
        "meter",
        "forward_cumulative_flow",
        "voltage_rtu",
        "low_battery",
        "valve_open",
        "leakage",
        "pipe_burst",
        "meter_time",
        "server_received_at",
    )
    list_filter = ("meter", "low_battery", "leakage", "pipe_burst", "valve_open", "meter_time")
    date_hierarchy = "meter_time"
