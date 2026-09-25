from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import pandas as pd
import numpy as np
import joblib
import json
import os
from datetime import datetime, timedelta

app = FastAPI(title="Livestock Health Surveillance AI API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Load models safely
def load_models():
    try:
        rf_model = joblib.load("models/rf_model.pkl")
        scaler = joblib.load("models/scaler.pkl")
        iso_model = joblib.load("models/iso_model.pkl")
        with open("models/metrics.json", "r") as f:
            metrics = json.load(f)
        return rf_model, scaler, iso_model, metrics
    except Exception as e:
        print(f"Error loading models: {e}")
        return None, None, None, None

rf_model, scaler, iso_model, metrics = load_models()

FEATURE_COLS = [
    "animal_population", "affected_animals", "new_cases", "deaths", 
    "vaccination_coverage", "temperature", "rainfall", "humidity", 
    "animal_density", "previous_cases", "cases_growth_rate"
]

class PredictRequest(BaseModel):
    disease: str
    district: str
    time_range: str
    animal_population: float
    affected_animals: float
    new_cases: float
    deaths: float
    vaccination_coverage: float
    temperature: float
    rainfall: float
    humidity: float
    animal_density: float
    previous_cases: float
    cases_growth_rate: float

@app.post("/api/predict")
async def predict_risk(req: PredictRequest):
    if rf_model is None:
        raise HTTPException(status_code=503, detail="AI model unavailable — prediction cannot be generated.")
    
    # Create feature array
    input_data = pd.DataFrame([{
        "animal_population": req.animal_population,
        "affected_animals": req.affected_animals,
        "new_cases": req.new_cases,
        "deaths": req.deaths,
        "vaccination_coverage": req.vaccination_coverage,
        "temperature": req.temperature,
        "rainfall": req.rainfall,
        "humidity": req.humidity,
        "animal_density": req.animal_density,
        "previous_cases": req.previous_cases,
        "cases_growth_rate": req.cases_growth_rate
    }])
    
    # Scale input
    input_scaled = scaler.transform(input_data)
    
    # Predict probability
    prob = rf_model.predict_proba(input_scaled)[0][1]
    risk_score = round(prob * 100, 1)
    
    if risk_score > 60:
        risk_level = "High Risk"
    elif risk_score > 30:
        risk_level = "Moderate Risk"
    else:
        risk_level = "Low Risk"
        
    trend = "Increasing" if req.cases_growth_rate > 0.1 else ("Stable" if req.cases_growth_rate > -0.1 else "Decreasing")
    
    # Calculate feature importances based on this prediction
    importances = rf_model.feature_importances_
    features_impact = []
    for i, col in enumerate(FEATURE_COLS):
        # Rough heuristic for direction based on correlation
        direction = "High" if input_data[col][0] > 0.5 * (req.animal_population if "animals" in col else 50) else "Low"
        features_impact.append({
            "factor": col.replace("_", " ").title(),
            "impact": round(float(importances[i]), 3),
            "value": float(input_data[col][0])
        })
    
    # Sort and take top 4
    features_impact.sort(key=lambda x: x["impact"], reverse=True)
    top_factors = features_impact[:4]
    
    # Dynamic recommended actions
    actions = []
    if risk_level == "High Risk":
        actions.append("Immediate Veterinary inspection")
        actions.append("Implement quarantine measures")
    if req.vaccination_coverage < 0.6:
        actions.append("Initiate emergency vaccination campaign")
    if req.cases_growth_rate > 0.5:
        actions.append("Enhanced active surveillance")
    if not actions:
        actions = ["Routine monitoring", "Promote biosecurity awareness"]
        
    horizon = int(req.time_range) if req.time_range.isdigit() else 14
        
    return {
        "disease": req.disease,
        "district": req.district,
        "risk_score": risk_score,
        "probability": round(float(prob), 3),
        "risk_level": risk_level,
        "confidence": metrics["accuracy"],
        "trend": trend,
        "predicted_cases": int(req.new_cases * (1 + req.cases_growth_rate)),
        "prediction_horizon_days": horizon,
        "top_risk_factors": top_factors,
        "recommended_actions": actions,
        "model_version": "v1.0"
    }

@app.get("/api/model-performance")
async def model_performance():
    if metrics is None:
        raise HTTPException(status_code=503, detail="Model metrics not found.")
    return metrics

class OutbreakRequest(BaseModel):
    new_cases: float
    cases_growth_rate: float
    deaths: float
    district: str

@app.post("/api/outbreak-detection")
async def detect_outbreak(req: OutbreakRequest):
    if iso_model is None:
        raise HTTPException(status_code=503, detail="Isolation Forest model unavailable.")
        
    input_data = pd.DataFrame([{
        "new_cases": req.new_cases,
        "cases_growth_rate": req.cases_growth_rate,
        "deaths": req.deaths
    }])
    
    # iso_model returns -1 for anomaly, 1 for normal
    prediction = iso_model.predict(input_data)[0]
    is_anomaly = prediction == -1
    
    # Compute an anomaly score (decision_function returns negative for anomalies usually)
    score = iso_model.decision_function(input_data)[0]
    
    severity = "Normal"
    if is_anomaly:
        severity = "High" if score < -0.1 else "Moderate"
        
    return {
        "outbreak_detected": bool(is_anomaly),
        "severity": severity,
        "anomaly_score": round(float(score), 3),
        "case_growth": req.cases_growth_rate,
        "affected_districts": [req.district] if is_anomaly else []
    }

class ForecastRequest(BaseModel):
    historical_cases: list[float]
    horizon: int

@app.post("/api/forecast")
async def forecast(req: ForecastRequest):
    # Simple autoregressive mock approach for the demo
    # We take the trend of the last few points and project it forward
    if not req.historical_cases:
        raise HTTPException(status_code=400, detail="No historical cases provided.")
        
    if len(req.historical_cases) < 2:
        trend = 0
    else:
        trend = (req.historical_cases[-1] - req.historical_cases[0]) / len(req.historical_cases)
        
    forecast_points = []
    last_val = req.historical_cases[-1]
    
    base_date = datetime.now()
    
    for i in range(req.horizon):
        next_val = max(0, last_val + trend + np.random.normal(0, max(1, last_val * 0.1)))
        forecast_points.append({
            "date": (base_date + timedelta(days=i+1)).strftime("%Y-%m-%d"),
            "predicted_cases": int(next_val)
        })
        last_val = next_val
        
    return {
        "forecast": forecast_points
    }

class ClusterRequest(BaseModel):
    districts: list[str]

@app.post("/api/cluster")
async def spatiotemporal_clustering(req: ClusterRequest):
    # Mocking real cluster logic with predefined coordinates to return real structural data
    # (In a real system, this would apply DBSCAN to lat/long/time rows)
    
    districts = req.districts if req.districts else ["Pune", "Satara", "Nashik"]
    clusters = []
    
    coords = {
        "Pune": [18.5204, 73.8567],
        "Satara": [17.6805, 74.0183],
        "Nashik": [20.0110, 73.7903],
        "Nagpur": [21.1458, 79.0882]
    }
    
    for i, dist in enumerate(districts):
        if dist in coords:
            lat, lng = coords[dist]
            clusters.append({
                "cluster_id": f"CLUST-{i+100}",
                "district": dist,
                "lat": lat + np.random.normal(0, 0.05),
                "lng": lng + np.random.normal(0, 0.05),
                "cases": np.random.randint(20, 150),
                "risk_level": "High Risk" if np.random.random() > 0.5 else "Moderate Risk",
                "latest_case": datetime.now().strftime("%Y-%m-%d")
            })
            
    return {"clusters": clusters}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
