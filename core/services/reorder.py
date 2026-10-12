import csv
import math
from pathlib import Path
from typing import Dict, List, Optional
import numpy as np
from django.conf import settings
from core.models import Product


def get_forecast_map() -> Dict[str, List[dict]]:
    """Loads ml/forecast.csv into a dictionary: {sku: [{'week_start': ..., 'forecast_units': ...}]}"""
    forecast_file = settings.BASE_DIR / "ml" / "forecast.csv"
    if not forecast_file.exists():
        return {}

    forecasts = {}
    with open(forecast_file, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            sku = row["sku"].strip()
            w_start = row["week_start"].strip()
            units = float(row["forecast_units"].strip())
            if sku not in forecasts:
                forecasts[sku] = []
            forecasts[sku].append({
                "week_start": w_start,
                "forecast_units": units,
            })
    return forecasts


def get_lead_times() -> Dict[str, int]:
    """Loads data/lead_times.csv: {sku: lead_time_weeks}"""
    data_dir = settings.DATA_DIR
    lead_file = data_dir / "lead_times.csv"
    if not lead_file.exists():
        return {}

    lead_times = {}
    with open(lead_file, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            lead_times[row["sku"].strip()] = int(row["lead_time_weeks"].strip())
    return lead_times


def get_sales_sigma_map() -> Dict[str, float]:
    """Calculates sample standard deviation (ddof=1) of units_sold for the most recent 12 weeks per SKU."""
    data_dir = settings.DATA_DIR
    sales_file = data_dir / "sales_history.csv"
    if not sales_file.exists():
        return {}

    sku_sales: Dict[str, List[tuple]] = {}
    with open(sales_file, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            sku = row["sku"].strip()
            week = row.get("week_start", "").strip()
            units = float(row["units_sold"].strip())
            if sku not in sku_sales:
                sku_sales[sku] = []
            sku_sales[sku].append((week, units))

    sigmas = {}
    for sku, records in sku_sales.items():
        records.sort(key=lambda x: x[0])
        recent_12 = [x[1] for x in records[-12:]]
        if len(recent_12) > 1:
            sigmas[sku] = float(np.std(recent_12, ddof=1))
        else:
            sigmas[sku] = 0.0
    return sigmas



def compute_reorder_suggestions() -> List[dict]:
    """Computes reorder suggestions for all 40 forecast SKUs following the contract formula."""
    lead_times = get_lead_times()
    forecasts = get_forecast_map()
    sigmas = get_sales_sigma_map()

    # Pre-fetch live stocks for all 40 SKUs in one query
    stocks = {p.sku: p.stock for p in Product.objects.filter(sku__in=lead_times.keys())}

    suggestions = []
    for sku, L in lead_times.items():
        current_stock = stocks.get(sku, 0)
        sku_forecast = forecasts.get(sku, [])

        # Lead time demand = sum of forecast_units for the first L forecast weeks
        # L can be up to 4
        capped_L = min(L, len(sku_forecast))
        lead_time_demand = round(sum(sku_forecast[i]["forecast_units"] for i in range(capped_L)), 1)

        sigma = sigmas.get(sku, 0.0)
        safety_stock = math.ceil(1.65 * sigma * math.sqrt(L))
        reorder_point = round(lead_time_demand + safety_stock, 1)
        reorder_qty = max(0, math.ceil(reorder_point - current_stock))

        suggestions.append({
            "sku": sku,
            "current_stock": current_stock,
            "lead_time_weeks": L,
            "lead_time_demand": lead_time_demand,
            "safety_stock": safety_stock,
            "reorder_point": reorder_point,
            "reorder_qty": reorder_qty,
        })

    return suggestions
