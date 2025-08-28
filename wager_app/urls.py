from django.urls import path
from . import views

urlpatterns = [
    path('webhook/', views.telegram_webhook, name='telegram_webhook'),
    path('yookassa-webhook/', views.yookassa_webhook, name='yookassa_webhook'),
]
