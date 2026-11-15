"""Demo ML helpers for Parkify parking-demand forecasts.

IMPORTANT: This model is trained on synthetic demonstration data, not verified
historical parking records. Its predictions illustrate how time, weekday,
occupancy, and price can influence demand. Do not use it for real-world
operational decisions without training and validating on real data.
"""
from datetime import datetime

import numpy as np
from sklearn.ensemble import RandomForestClassifier


LABELS = {0: "Low", 1: "Medium", 2: "High"}


def _time_demand_score(hour):
    """Illustrative daily demand pattern used only to create demo training data."""
    if 0 <= hour <= 6:
        return 12
    if 7 <= hour <= 8:
        return 25
    if 9 <= hour <= 11:
        return 48
    if 12 <= hour <= 14:
        return 78
    if 15 <= hour <= 17:
        return 48
    if 18 <= hour <= 20:
        return 82
    return 18  # 21:00-23:00


def _make_demo_model():
    rng = np.random.default_rng(42)
    rows = []
    labels = []

    # Generate varied synthetic examples. Time of day now has a meaningful
    # influence, while current occupancy, weekends, and price still matter.
    for _ in range(6000):
        hour = int(rng.integers(0, 24))
        weekday = int(rng.integers(0, 7))  # Monday=0
        occupancy = float(rng.uniform(0, 100))
        price = float(rng.uniform(20, 100))
        weekend = int(weekday >= 5)

        score = _time_demand_score(hour)
        score += occupancy * 0.25
        score += weekend * 7
        score += (50 - price) * 0.08
        score += float(rng.normal(0, 7))

        demand = 0 if score < 35 else (1 if score < 70 else 2)
        rows.append([hour, weekday, occupancy, price, weekend])
        labels.append(demand)

    model = RandomForestClassifier(
        n_estimators=160,
        random_state=42,
        min_samples_leaf=3,
        class_weight="balanced",
    )
    model.fit(np.asarray(rows), np.asarray(labels))
    return model


_MODEL = _make_demo_model()


def predict_demand(lot, hour=None, weekday=None):
    now = datetime.now()
    hour = now.hour if hour is None else int(hour)
    weekday = now.weekday() if weekday is None else int(weekday)

    # Keep inputs inside valid ranges.
    hour = max(0, min(23, hour))
    weekday = max(0, min(6, weekday))

    total = max(int(lot["total_slots"] or 1), 1)
    available = max(0, min(int(lot["available_slots"]), total))
    occupancy_pct = (total - available) / total * 100
    price = float(lot["price"])

    features = np.asarray([[
        hour,
        weekday,
        occupancy_pct,
        price,
        int(weekday >= 5),
    ]])

    class_id = int(_MODEL.predict(features)[0])
    probabilities = _MODEL.predict_proba(features)[0]
    confidence = round(float(max(probabilities)) * 100)

    # Illustrative availability estimate associated with the demand category.
    expected_occupancy = {0: 0.35, 1: 0.62, 2: 0.86}[class_id]
    estimated_available = round(total * (1 - expected_occupancy))
    estimated_available = max(0, min(total, estimated_available))

    return {
        "demand": LABELS[class_id],
        "class_id": class_id,
        "confidence": confidence,
        "estimated_available": estimated_available,
        "hour": hour,
        "weekday": weekday,
    }
