import asyncio
import pandas as pd
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# Serve frontend dashboard
@app.get("/")
async def get_index():
    return FileResponse("index.html")

# 1. Load and prepare data
gen_df = pd.read_csv("Plant_1_Generation_Data.csv")
weather_df = pd.read_csv("Plant_1_Weather_Sensor_Data.csv")

gen_df['DATE_TIME'] = pd.to_datetime(gen_df['DATE_TIME'], format='%d-%m-%Y %H:%M')
weather_df['DATE_TIME'] = pd.to_datetime(weather_df['DATE_TIME'], format='%Y-%m-%d %H:%M:%S')

weather_df = weather_df.drop(columns=['SOURCE_KEY'], errors='ignore')
df = pd.merge(gen_df, weather_df, on=["DATE_TIME", "PLANT_ID"], how="inner")
df = df.sort_values(by="DATE_TIME")

def calculate_twin_expected_power(irradiation, module_temp, dc_rating_kw=650):
    """Digital Twin mathematical energy balance model"""
    if irradiation <= 0.001:
        return 0.0
    gamma = -0.004 # -0.4%/°C temperature coefficient
    temp_loss = 1.0 + gamma * (module_temp - 25.0)
    expected_kw = dc_rating_kw * irradiation * temp_loss
    return max(0.0, expected_kw)

def diagnose_status(actual_dc, expected_dc, irradiation):
    """Fault Detection & Maintenance Diagnosis"""
    if irradiation < 0.05:
        return "IDLE", "Night / Low Sunlight", "No Action Required"
    
    if expected_dc > 50 and actual_dc < 5:
        return "FAULT", "Inverter Tripped / Disconnected", "🚨 Immediate Dispatch: Check Breaker & Inverter"
    
    ratio = (actual_dc / expected_dc) if expected_dc > 0 else 1.0
    
    if ratio >= 0.85:
        return "NORMAL", "Optimal Generation", "None (System Healthy)"
    elif 0.60 <= ratio < 0.85:
        return "WARNING", "Dust / Soiling Accumulation", "⚠️ Schedule Routine Panel Cleaning"
    else:
        return "FAULT", "Severe Shading / Hotspot", "⚠️ Inspect Panel Strings for Physical Obstructions"

@app.websocket("/ws")
async def live_solar_feed(websocket: WebSocket):
    await websocket.accept()
    print("Dashboard connected!")
    
    try:
        while True:
            unique_timestamps = df["DATE_TIME"].unique()
            
            for ts in unique_timestamps:
                step_data = df[df["DATE_TIME"] == ts]
                
                inverters = []
                plant_actual = 0.0
                plant_expected = 0.0
                
                for _, row in step_data.iterrows():
                    irr = float(row["IRRADIATION"])
                    mod_t = float(row["MODULE_TEMPERATURE"])
                    act_dc = float(row["DC_POWER"])
                    
                    exp_dc = calculate_twin_expected_power(irr, mod_t)
                    status, diag, action = diagnose_status(act_dc, exp_dc, irr)
                    
                    plant_actual += act_dc
                    plant_expected += exp_dc
                    
                    inverters.append({
                        "id": str(row["SOURCE_KEY"])[:8], # Short identifier
                        "actual_dc": round(act_dc, 1),
                        "expected_dc": round(exp_dc, 1),
                        "temp": round(mod_t, 1),
                        "status": status,
                        "diagnosis": diag,
                        "action": action
                    })
                    
                payload = {
                    "timestamp": pd.to_datetime(ts).strftime('%Y-%m-%d %H:%M'),
                    "ambient_temp": round(float(step_data.iloc[0]["AMBIENT_TEMPERATURE"]), 1),
                    "irradiation": round(float(step_data.iloc[0]["IRRADIATION"]), 3),
                    "total_actual": round(plant_actual, 1),
                    "total_expected": round(plant_expected, 1),
                    "inverters": inverters
                }
                
                await websocket.send_json(payload)
                await asyncio.sleep(1.0) # Stream 1 time-interval per second
    except WebSocketDisconnect:
        print("Dashboard disconnected.")
    except Exception as e:
        print(f"WebSocket session ended: {e}")

if __name__ == "__main__":
    import os
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("backend:app", host="0.0.0.0", port=port, reload=False)