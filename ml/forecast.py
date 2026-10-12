"""Demand Forecasting script for VIKMO DealerDesk AI/ML Intern Track.
Evaluates baselines, trains an ML model, and outputs ml/forecast.csv for 40 SKUs across 4 target weeks.
"""
import os
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_absolute_error, mean_squared_error

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
OUTPUT_FILE = BASE_DIR / "ml" / "forecast.csv"

TARGET_WEEKS = ["2026-09-28", "2026-10-05", "2026-10-12", "2026-10-19"]


def load_data():
    sales = pd.read_csv(DATA_DIR / "sales_history.csv")
    sales["week_start"] = pd.to_datetime(sales["week_start"])
    sales = sales.sort_values(["sku", "week_start"]).reset_index(drop=True)

    lead_times = pd.read_csv(DATA_DIR / "lead_times.csv")
    forecast_skus = lead_times["sku"].unique().tolist()

    promo = pd.read_csv(DATA_DIR / "promo_calendar.csv")
    promo["week_start"] = pd.to_datetime(promo["week_start"])

    return sales, forecast_skus, promo


def evaluate_and_train():
    sales, forecast_skus, promo = load_data()
    print(f"Loaded {len(sales)} sales records for {len(forecast_skus)} forecast SKUs.")

    # Filter to forecast SKUs
    sales = sales[sales["sku"].isin(forecast_skus)].copy()

    # Create lag features per SKU
    all_features = []
    for sku, group in sales.groupby("sku"):
        group = group.copy()
        for lag in [1, 2, 3, 4, 8, 12]:
            group[f"lag_{lag}"] = group["units_sold"].shift(lag)
        group["rolling_mean_4"] = group["units_sold"].shift(1).rolling(4).mean()
        group["rolling_mean_12"] = group["units_sold"].shift(1).rolling(12).mean()
        all_features.append(group)

    feat_df = pd.concat(all_features).dropna().reset_index(drop=True)

    # Train / Validation split (Last 4 weeks as holdout)
    unique_weeks = sorted(feat_df["week_start"].unique())
    val_weeks = unique_weeks[-4:]
    train_df = feat_df[~feat_df["week_start"].isin(val_weeks)]
    val_df = feat_df[feat_df["week_start"].isin(val_weeks)]

    feature_cols = ["promo_flag", "lag_1", "lag_2", "lag_3", "lag_4", "rolling_mean_4", "rolling_mean_12"]

    # Baseline 1: Naive (Lag 1)
    naive_mae = mean_absolute_error(val_df["units_sold"], val_df["lag_1"])
    print(f"Baseline (Lag-1) MAE: {naive_mae:.2f}")

    # Baseline 2: 4-week Rolling Mean
    roll_mae = mean_absolute_error(val_df["units_sold"], val_df["rolling_mean_4"])
    print(f"Baseline (Rolling-4) MAE: {roll_mae:.2f}")

    # Model: Ridge Regression
    model = Ridge(alpha=1.0)
    model.fit(train_df[feature_cols], train_df["units_sold"])
    val_preds = model.predict(val_df[feature_cols])
    val_preds = np.clip(val_preds, 0, None)
    ridge_mae = mean_absolute_error(val_df["units_sold"], val_preds)
    ridge_rmse = np.sqrt(mean_squared_error(val_df["units_sold"], val_preds))
    print(f"Ridge Regression MAE: {ridge_mae:.2f}, RMSE: {ridge_rmse:.2f}")

    # Retrain on full historical dataset
    full_model = Ridge(alpha=1.0)
    full_model.fit(feat_df[feature_cols], feat_df["units_sold"])

    # Iterative multi-step forecasting for the 4 target weeks
    forecast_rows = []
    for sku in forecast_skus:
        sku_sales = sales[sales["sku"] == sku].copy()
        history = sku_sales["units_sold"].tolist()
        
        for w_str in TARGET_WEEKS:
            w_dt = pd.to_datetime(w_str)
            p_flag = 0
            match = promo[(promo["sku"] == sku) & (promo["week_start"] == w_dt)]
            if not match.empty:
                p_flag = int(match.iloc[0]["promo_flag"])

            lag_1 = history[-1] if len(history) >= 1 else 0
            lag_2 = history[-2] if len(history) >= 2 else lag_1
            lag_3 = history[-3] if len(history) >= 3 else lag_2
            lag_4 = history[-4] if len(history) >= 4 else lag_3
            roll_4 = np.mean(history[-4:]) if len(history) >= 4 else lag_1
            roll_12 = np.mean(history[-12:]) if len(history) >= 12 else roll_4

            X_step = pd.DataFrame([{
                "promo_flag": p_flag,
                "lag_1": lag_1,
                "lag_2": lag_2,
                "lag_3": lag_3,
                "lag_4": lag_4,
                "rolling_mean_4": roll_4,
                "rolling_mean_12": roll_12,
            }])

            pred = float(full_model.predict(X_step)[0])
            pred = max(0.0, round(pred, 1))

            forecast_rows.append({
                "sku": sku,
                "week_start": w_str,
                "forecast_units": pred,
            })
            history.append(pred)

    forecast_df = pd.DataFrame(forecast_rows)
    os.makedirs(OUTPUT_FILE.parent, exist_ok=True)
    forecast_df.to_csv(OUTPUT_FILE, index=False)
    print(f"Saved {len(forecast_df)} forecast rows to {OUTPUT_FILE}")
    assert len(forecast_df) == 160, f"Expected 160 rows, got {len(forecast_df)}"


if __name__ == "__main__":
    evaluate_and_train()
