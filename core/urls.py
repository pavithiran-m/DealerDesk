from django.urls import path
from . import views

urlpatterns = [
    # Health check
    path("health/", views.health),

    # Products API
    path("products/", views.product_list),
    path("products/<str:sku>/", views.product_detail),

    # Inventory API
    path("inventory/<str:sku>/", views.inventory_update),

    # Dealers API
    path("dealers/", views.dealer_list),
    path("dealers/<int:pk>/", views.dealer_detail),

    # Orders API
    path("orders/", views.order_list),
    path("orders/<str:order_number>/", views.order_detail),
    path("orders/<str:order_number>/confirm/", views.order_confirm),
    path("orders/<str:order_number>/dispatch/", views.order_dispatch),
    path("orders/<str:order_number>/deliver/", views.order_deliver),
    path("orders/<str:order_number>/cancel/", views.order_cancel),

    # Supplier sync API
    path("sync/supplier/", views.supplier_sync),

    # AI Dealer Assistant
    path("assistant/chat/", views.assistant_chat),

    # ML Demand Forecasting & Reorder Suggestions
    path("forecast/<str:sku>/", views.forecast_detail),
    path("reorder-suggestions/", views.reorder_suggestions),
]

