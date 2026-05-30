import json
import logging
import uuid
from datetime import datetime
from decimal import Decimal

import requests
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.db.models import Count, Q

from django.http import JsonResponse, HttpResponseNotAllowed
from django.shortcuts import render, get_object_or_404, redirect
from django.utils.dateparse import parse_datetime
from django.views.decorators.csrf import csrf_exempt

from .forms import WaterMeterForm, CustomerRegistrationForm, UserUpdateForm
from .models import WaterMeter, MeterTelemetry, Profile, Payment

logger = logging.getLogger(__name__)


def _call_waafipay(payload: dict) -> dict:
    resp = requests.post(
        settings.WAAFIPAY_BASE_URL,
        json=payload,
        headers={"Content-Type": "application/json"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def _deduct_balance(meter: WaterMeter):
    prev = (
        MeterTelemetry.objects.filter(meter=meter)
        .exclude(forward_cumulative_flow=0)
        .order_by("-meter_time")
    )
    if prev.count() < 2:
        return
    latest = prev[0]
    previous = prev[1]
    diff = latest.forward_cumulative_flow - previous.forward_cumulative_flow
    if diff <= 0:
        return
    cost = Decimal(str(round(diff * settings.WATER_TARIFF_PER_M3, 2)))
    meter.balance = max(meter.balance - cost, Decimal("0.00"))
    meter.save(update_fields=["balance"])
    logger.info(
        "Deducted %s from %s (%.3f m³ @ %s/m³). New balance: %s",
        cost,
        meter.device_eui,
        diff,
        settings.WATER_TARIFF_PER_M3,
        meter.balance,
    )
    if meter.balance <= Decimal("0.00"):
        logger.warning(
            "Balance exhausted for %s — valve should be closed", meter.device_eui
        )


def login_view(request):
    if request.user.is_authenticated:
        return redirect("dashboard")
    if request.method == "POST":
        username = request.POST.get("username")
        password = request.POST.get("password")
        user = authenticate(request, username=username, password=password)
        if user is not None:
            login(request, user)
            return redirect("dashboard")
        messages.error(request, "Invalid username or password.")
    return render(request, "water_meter/registration/login.html")


def logout_view(request):
    logout(request)
    messages.success(request, "Logged out successfully.")
    return redirect("login")


def customer_register(request):
    if request.user.is_authenticated:
        return redirect("dashboard")
    if request.method == "POST":
        form = CustomerRegistrationForm(request.POST)
        if form.is_valid():
            cd = form.cleaned_data
            user = User.objects.create_user(
                username=cd["username"],
                email=cd["email"],
                password=cd["password"],
            )
            Profile.objects.create(user=user, phone=cd.get("phone", ""))
            WaterMeter.objects.create(
                device_eui=cd["device_eui"],
                location=cd.get("location", ""),
                owner=user,
            )
            login(request, user)
            messages.success(
                request, f"Account created and meter {cd['device_eui']} registered."
            )
            return redirect("dashboard")
    else:
        form = CustomerRegistrationForm()
    return render(request, "water_meter/customer_register.html", {"form": form})


@login_required
def dashboard(request):
    if request.user.is_superuser:
        meters = WaterMeter.objects.select_related("owner").all()
        alarm_meters = WaterMeter.objects.filter(telemetry__low_battery=True).distinct()
    else:
        meters = WaterMeter.objects.filter(owner=request.user)
        alarm_meters = WaterMeter.objects.filter(
            owner=request.user,
            telemetry__low_battery=True,
        ).distinct()
    latest = {}
    for m in meters:
        t = m.telemetry.order_by("-meter_time").first()
        if t:
            latest[m.id] = t
    ctx = {
        "meters": meters,
        "latest": latest,
        "total_meters": meters.count(),
        "alarm_count": alarm_meters.count(),
    }
    return render(request, "water_meter/dashboard.html", ctx)


@login_required
def top_up(request, meter_id):
    meter = _get_meter_or_404(meter_id, request.user)
    if request.method == "POST":
        amount = request.POST.get("amount")
        phone = request.POST.get("phone", "").strip()
        if not amount or not phone:
            messages.error(request, "Amount and phone number are required.")
            return render(request, "water_meter/top_up.html", {"meter": meter})
        try:
            amount = str(round(float(amount), 2))
        except ValueError:
            messages.error(request, "Invalid amount.")
            return render(request, "water_meter/top_up.html", {"meter": meter})
        ref_id = f"WM-{meter.id}-{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6]}"
        Payment.objects.create(
            meter=meter,
            user=request.user,
            reference_id=ref_id,
            amount=amount,
            currency=settings.WAAFIPAY_CURRENCY,
            phone=phone,
        )
        waafipay_payload = {
            "schemaVersion": "1.0",
            "requestId": uuid.uuid4().hex,
            "timestamp": datetime.now().isoformat(),
            "channelName": "WEB",
            "serviceName": "API_PURCHASE",
            "serviceParams": {
                "merchantUid": settings.WAAFIPAY_MERCHANT_UID,
                "apiUserId": settings.WAAFIPAY_API_USER_ID,
                "apiKey": settings.WAAFIPAY_API_KEY,
                "paymentMethod": "MWALLET_ACCOUNT",
                "payerInfo": {"accountNo": phone},
                "transactionInfo": {
                    "referenceId": ref_id,
                    "invoiceId": ref_id,
                    "amount": amount,
                    "currency": settings.WAAFIPAY_CURRENCY,
                    "description": f"Lora-Water top-up for meter {meter.device_eui}",
                },
            },
        }
        try:
            result = _call_waafipay(waafipay_payload)
            logger.info("WaafiPay response: %s", result)
            messages.success(
                request, "Payment request sent. Check your phone to complete."
            )
        except requests.RequestException as e:
            logger.error("WaafiPay call failed: %s", e)
            messages.error(request, "Payment service unavailable. Try again later.")
        return redirect("meter-detail", meter_id=meter.id)
    return render(request, "water_meter/top_up.html", {"meter": meter})


@csrf_exempt
def waafipay_webhook(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    try:
        data = json.loads(request.body)
    except ValueError:
        return JsonResponse({"status": "error"}, status=400)
    event = data.get("event")
    payment = data.get("payment", {})
    ref_id = payment.get("reference_id", "")
    status = payment.get("status", "").lower()
    transaction_id = payment.get("transaction_id", "")
    if event == "webhook.test":
        return JsonResponse({"status": "ok"})
    if not ref_id:
        return JsonResponse(
            {"status": "error", "message": "Missing reference_id"}, status=400
        )
    try:
        pay = Payment.objects.get(reference_id=ref_id)
    except Payment.DoesNotExist:
        logger.warning("Payment not found for reference: %s", ref_id)
        return JsonResponse(
            {"status": "error", "message": "Payment not found"}, status=404
        )
    pay.waafipay_transaction_id = str(transaction_id)
    pay.status = status
    pay.save(update_fields=["waafipay_transaction_id", "status"])
    if status == "approved":
        meter = pay.meter
        meter.balance += pay.amount
        meter.save(update_fields=["balance"])
        logger.info(
            "Credited %s %s to meter %s. New balance: %s",
            pay.amount,
            pay.currency,
            meter.device_eui,
            meter.balance,
        )
    return JsonResponse({"status": "ok"})


@login_required
def payment_history(request, meter_id):
    meter = _get_meter_or_404(meter_id, request.user)
    payments = meter.payments.all()
    return render(
        request,
        "water_meter/payment_history.html",
        {"meter": meter, "payments": payments},
    )


@login_required
def meter_detail(request, meter_id):
    meter = _get_meter_or_404(meter_id, request.user)
    telemetry = meter.telemetry.order_by("-meter_time")[:50]
    ctx = {
        "meter": meter,
        "telemetry": telemetry,
    }
    return render(request, "water_meter/meter_detail.html", ctx)


@login_required
def user_list(request):
    if not request.user.is_superuser:
        messages.error(request, "Access denied.")
        return redirect("dashboard")
    q = request.GET.get("q", "").strip()
    users = User.objects.annotate(meter_count=Count("meters"))
    if q:
        users = users.filter(
            Q(username__icontains=q)
            | Q(email__icontains=q)
            | Q(profile__phone__icontains=q)
        )
    users = users.order_by("username")
    ctx = {"users": users, "q": q}
    return render(request, "water_meter/user_list.html", ctx)


@login_required
def user_detail(request, user_id):
    if not request.user.is_superuser:
        messages.error(request, "Access denied.")
        return redirect("dashboard")
    u = get_object_or_404(User, id=user_id)
    meters = u.meters.all()
    ctx = {"u": u, "meters": meters}
    return render(request, "water_meter/user_detail.html", ctx)


@login_required
def user_update(request, user_id):
    if not request.user.is_superuser:
        messages.error(request, "Access denied.")
        return redirect("dashboard")
    u = get_object_or_404(User, id=user_id)
    if request.method == "POST":
        form = UserUpdateForm(request.POST, instance=u)
        if form.is_valid():
            form.save()
            phone = form.cleaned_data.get("phone", "")
            profile, _ = Profile.objects.get_or_create(user=u)
            profile.phone = phone
            profile.save(update_fields=["phone"])
            messages.success(request, f"User {u.username} updated.")
            return redirect("user-detail", user_id=u.id)
    else:
        form = UserUpdateForm(instance=u)
    return render(request, "water_meter/user_form.html", {"form": form, "u": u})


@login_required
def meter_list(request):
    q = request.GET.get("q", "").strip()
    if request.user.is_superuser:
        meters = WaterMeter.objects.select_related("owner").all()
    else:
        meters = WaterMeter.objects.filter(owner=request.user)
    if q:
        meters = meters.filter(
            Q(device_eui__icontains=q)
            | Q(location__icontains=q)
            | Q(owner__username__icontains=q)
        )
    latest = {}
    for m in meters:
        t = m.telemetry.order_by("-meter_time").first()
        if t:
            latest[m.id] = t
    ctx = {
        "meters": meters,
        "latest": latest,
        "q": q,
    }
    return render(request, "water_meter/meter_list.html", ctx)


@login_required
def user_delete(request, user_id):
    if not request.user.is_superuser:
        messages.error(request, "Access denied.")
        return redirect("dashboard")
    u = get_object_or_404(User, id=user_id)
    if request.method == "POST":
        name = u.username
        u.delete()
        messages.success(request, f"User {name} deleted.")
        return redirect("user-list")
    return render(request, "water_meter/user_confirm_delete.html", {"u": u})


@login_required
def meter_create(request):
    if request.method == "POST":
        form = WaterMeterForm(request.POST)
        if form.is_valid():
            meter = form.save(commit=False)
            meter.owner = request.user
            meter.save()
            messages.success(request, f"Meter {meter.device_eui} registered.")
            return redirect("dashboard")
    else:
        form = WaterMeterForm()
    return render(
        request,
        "water_meter/meter_form.html",
        {"form": form, "title": "Register Meter"},
    )


@login_required
def meter_edit(request, meter_id):
    meter = _get_meter_or_404(meter_id, request.user)
    if request.method == "POST":
        form = WaterMeterForm(request.POST, instance=meter)
        if form.is_valid():
            form.save()
            messages.success(request, "Meter updated.")
            return redirect("meter-detail", meter_id=meter.id)
    else:
        form = WaterMeterForm(instance=meter)
    return render(
        request, "water_meter/meter_form.html", {"form": form, "title": "Edit Meter"}
    )


@login_required
def meter_delete(request, meter_id):
    meter = _get_meter_or_404(meter_id, request.user)
    if request.method == "POST":
        eui = meter.device_eui
        meter.delete()
        messages.success(request, f"Meter {eui} deleted.")
        return redirect("dashboard")
    return render(request, "water_meter/meter_confirm_delete.html", {"meter": meter})


@csrf_exempt
def lorawan_c2_webhook(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    try:
        payload = json.loads(request.body)
    except ValueError:
        return JsonResponse({"status": "error", "message": "Invalid JSON"}, status=400)

    device_eui = payload.get("end_device_ids", {}).get("dev_eui") or payload.get(
        "device_eui"
    )

    uplink_message = payload.get("uplink_message", {})
    decoded = uplink_message.get("decoded_payload", {})

    if not device_eui:
        return JsonResponse(
            {"status": "error", "message": "Missing device_eui"}, status=400
        )

    if not decoded:
        return JsonResponse(
            {"status": "error", "message": "Missing decoded_payload"}, status=400
        )

    try:
        meter = WaterMeter.objects.get(device_eui=device_eui)
    except WaterMeter.DoesNotExist:
        return JsonResponse(
            {"status": "error", "message": "Device not registered"}, status=404
        )

    alarms = decoded.get("alarms", {})

    meter_time_raw = decoded.get("meter_timestamp")
    meter_time = parse_datetime(meter_time_raw) if meter_time_raw else None
    if meter_time is None and meter_time_raw:
        meter_time = _parse_custom_timestamp(meter_time_raw)

    MeterTelemetry.objects.create(
        meter=meter,
        forward_instantaneous_flow=decoded.get("forward_instantaneous_flow", 0.0),
        forward_cumulative_flow=decoded.get("forward_cumulative_flow", 0.0),
        reverse_instantaneous_flow=decoded.get("reverse_instantaneous_flow", 0.0),
        reverse_cumulative_flow=decoded.get("reverse_cumulative_flow", 0.0),
        voltage_rtu=decoded.get("voltage_rtu", 0.0),
        voltage_meter=decoded.get("voltage_meter", 0.0),
        memory_alarm=alarms.get("memory_alarm", False),
        flow_meter_alarm=alarms.get("flow_meter_alarm", False),
        low_battery=alarms.get("low_battery", False),
        valve_open=alarms.get("valve_open", False),
        magnetic_attack=alarms.get("magnetic_attack", False),
        leakage=alarms.get("leakage", False),
        pipe_burst=alarms.get("pipe_burst", False),
        validity_invalid=alarms.get("validity_invalid", False),
        meter_time=meter_time,
    )

    _deduct_balance(meter)

    return JsonResponse({"status": "success"}, status=201)


def _parse_custom_timestamp(raw: str):
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _get_meter_or_404(meter_id, user):
    if user.is_superuser:
        return get_object_or_404(WaterMeter, id=meter_id)
    return get_object_or_404(WaterMeter, id=meter_id, owner=user)


@login_required
def toggle_valve(request, meter_id, action):
    meter = _get_meter_or_404(meter_id, request.user)

    control_byte = "7E" if action == "open" else "69" if action == "close" else None
    if control_byte is None:
        return JsonResponse(
            {"status": "error", "message": "Invalid action"}, status=400
        )

    addr_bytes = meter.device_eui.rjust(10, "0")
    addr_pairs = " ".join(addr_bytes[i : i + 2] for i in range(0, 10, 2))

    raw_payload_hex = f"68 0D {addr_pairs} D7 {control_byte} ss mm hh DD MM YY CS 16"

    logger.info(
        "Valve %s for %s — raw payload: %s", action, meter.device_eui, raw_payload_hex
    )

    messages.success(request, f"Valve {action} command sent for {meter.device_eui}")
    return redirect("meter-detail", meter_id=meter.id)
