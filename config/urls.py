from django.contrib import admin
from django.urls import path, include
from water_meter import views as wm_views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("login/", wm_views.login_view, name="login"),
    path("register/", wm_views.customer_register, name="customer-register"),
    path("", include("water_meter.urls")),
]
