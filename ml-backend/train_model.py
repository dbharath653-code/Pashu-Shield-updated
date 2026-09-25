import pandas as pd
import numpy as np
from sklearn.ensemble import RandomForestClassifier, IsolationForest
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score, precision_score, recall_score, f1_score, roc_auc_score
import joblib
import json
import os
from datetime import datetime

os.makedirs("models", exist_ok=True)

print("Generating synthetic demo data...")
np.random.seed(42)

n_samples = 5000
districts = ["Pune", "Satara", "Aurangabad", "Nagpur", "Nashik", "Nanded", "Latur", "Solapur", "Kolhapur", "Thane"]
diseases = ["LSD", "FMD", "HS", "BQ", "PPR"]

# Generate features
data = {
    "district": np.random.choice(districts, n_samples),
    "disease": np.random.choice(diseases, n_samples),
    "animal_population": np.random.randint(500, 50000, n_samples),
    "affected_animals": np.random.randint(0, 1000, n_samples),
    "new_cases": np.random.randint(0, 150, n_samples),
    "deaths": np.random.randint(0, 50, n_samples),
    "vaccination_coverage": np.random.uniform(0.1, 0.95, n_samples),
    "temperature": np.random.uniform(20.0, 42.0, n_samples),
    "rainfall": np.random.uniform(0.0, 200.0, n_samples),
    "humidity": np.random.uniform(30.0, 95.0, n_samples),
    "animal_density": np.random.uniform(10.0, 500.0, n_samples),
    "previous_cases": np.random.randint(0, 500, n_samples),
    "cases_growth_rate": np.random.uniform(-0.5, 2.5, n_samples),
}
df = pd.DataFrame(data)

# Create a realistic "outbreak_risk" probability target
# Risk increases with high cases, high growth, low vaccination, high density, and specific weather
risk_score = (
    (df["new_cases"] / 150) * 0.25 + 
    (df["cases_growth_rate"] / 2.5) * 0.20 + 
    (1 - df["vaccination_coverage"]) * 0.20 +
    (df["affected_animals"] / 1000) * 0.15 +
    (df["animal_density"] / 500) * 0.10 +
    (df["temperature"] / 42) * 0.05 +
    (df["humidity"] / 95) * 0.05
)

# Add some noise
risk_score += np.random.normal(0, 0.05, n_samples)
risk_score = np.clip(risk_score, 0, 1)

df["target_probability"] = risk_score
# Binary target for classification (threshold 0.5)
df["outbreak_risk"] = (risk_score > 0.5).astype(int)

# Select features for training
feature_cols = [
    "animal_population", "affected_animals", "new_cases", "deaths", 
    "vaccination_coverage", "temperature", "rainfall", "humidity", 
    "animal_density", "previous_cases", "cases_growth_rate"
]
X = df[feature_cols]
y = df["outbreak_risk"]

print("Splitting dataset and scaling...")
X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)

scaler = StandardScaler()
X_train_scaled = scaler.fit_transform(X_train)
X_test_scaled = scaler.transform(X_test)

print("Training Random Forest Classifier...")
rf = RandomForestClassifier(n_estimators=100, max_depth=10, random_state=42)
rf.fit(X_train_scaled, y_train)

# Calculate metrics
y_pred = rf.predict(X_test_scaled)
y_prob = rf.predict_proba(X_test_scaled)[:, 1]

metrics = {
    "model": "Random Forest",
    "accuracy": round(accuracy_score(y_test, y_pred), 3),
    "precision": round(precision_score(y_test, y_pred), 3),
    "recall": round(recall_score(y_test, y_pred), 3),
    "f1_score": round(f1_score(y_test, y_pred), 3),
    "roc_auc": round(roc_auc_score(y_test, y_prob), 3),
    "training_samples": len(X_train),
    "last_trained": datetime.now().isoformat()
}
print(f"Metrics: {metrics}")

print("Training Isolation Forest for Outbreak Detection (Anomaly Detection)...")
# Train on cases, growth rate, and deaths
iso_features = ["new_cases", "cases_growth_rate", "deaths"]
iso_model = IsolationForest(contamination=0.05, random_state=42)
iso_model.fit(df[iso_features])

print("Saving models to disk...")
joblib.dump(rf, "models/rf_model.pkl")
joblib.dump(scaler, "models/scaler.pkl")
joblib.dump(iso_model, "models/iso_model.pkl")
with open("models/metrics.json", "w") as f:
    json.dump(metrics, f)

print("Training complete!")
