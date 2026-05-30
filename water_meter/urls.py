from django.urls import path
from . import views

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("users/", views.user_list, name="user-list"),
    path("users/<int:user_id>/", views.user_detail, name="user-detail"),
    path("users/<int:user_id>/edit/", views.user_update, name="user-update"),
    path("users/<int:user_id>/delete/", views.user_delete, name="user-delete"),
    path("meters/", views.meter_list, name="meter-list"),
    path("logout/", views.logout_view, name="logout"),
    path("meter/new/", views.meter_create, name="meter-create"),
    path("meter/<int:meter_id>/", views.meter_detail, name="meter-detail"),
    path("meter/<int:meter_id>/edit/", views.meter_edit, name="meter-edit"),
    path("meter/<int:meter_id>/delete/", views.meter_delete, name="meter-delete"),
    path("valve/<int:meter_id>/<str:action>/", views.toggle_valve, name="toggle-valve"),
    path("webhook/lorawan/", views.lorawan_c2_webhook, name="lorawan-webhook"),
    path("webhook/waafipay/", views.waafipay_webhook, name="waafipay-webhook"),
    path("meter/<int:meter_id>/top-up/", views.top_up, name="top-up"),
    path("meter/<int:meter_id>/payments/", views.payment_history, name="payment-history"),
]
