from datetime import datetime, timezone
from uuid import uuid4

from flask import Flask, jsonify, request
from pymongo import MongoClient

app = Flask(__name__)

client = MongoClient("mongodb://localhost:27017")
db = client["seva_demo"]

cases = db["cases"]
hospitals = db["hospitals"]
ambulances = db["ambulances"]


def seed_demo_data():
    if hospitals.count_documents({}) == 0:
        hospitals.insert_many([
            {
                "hospital_id": "H001",
                "name": "Central General Hospital",
                "latitude": 12.9750,
                "longitude": 77.6050,
                "facilities": ["emergency", "cardiac", "icu"],
                "available": True,
            },
            {
                "hospital_id": "H002",
                "name": "Southside Medical Centre",
                "latitude": 12.9400,
                "longitude": 77.5900,
                "facilities": ["emergency", "trauma"],
                "available": True,
            },
        ])

    if ambulances.count_documents({}) == 0:
        ambulances.insert_many([
            {
                "ambulance_id": "A001",
                "name": "Demo Ambulance 1",
                "status": "available",
                "latitude": 12.9716,
                "longitude": 77.5946,
                "case_id": None,
            },
            {
                "ambulance_id": "A002",
                "name": "Demo Ambulance 2",
                "status": "available",
                "latitude": 12.9500,
                "longitude": 77.6000,
                "case_id": None,
            },
        ])


@app.get("/")
def home():
    return jsonify({"message": "SEVA API is running"})


@app.get("/api/health")
def health():
    try:
        client.admin.command("ping")
        return jsonify({"status": "ok", "database": "connected"})
    except Exception as error:
        return jsonify({"status": "error", "message": str(error)}), 500


@app.post("/api/cases")
def create_case():
    data = request.get_json(silent=True) or {}

    required = ["patient_name", "caller_name", "symptoms"]
    missing = [field for field in required if not data.get(field)]

    if missing:
        return jsonify({
            "error": "Missing required fields",
            "fields": missing,
        }), 400

    case = {
        "case_id": str(uuid4()),
        "patient_name": data["patient_name"],
        "caller_name": data["caller_name"],
        "symptoms": data["symptoms"],
        "risk_level": "Needs review",
        "pickup_latitude": data.get("pickup_latitude"),
        "pickup_longitude": data.get("pickup_longitude"),
        "hospital_id": None,
        "ambulance_id": None,
        "case_status": "requested",
        "route_stage": "Waiting for ambulance",
        "traffic_level": "Unknown",
        "eta_minutes": None,
        "ambulance_latitude": None,
        "ambulance_longitude": None,
        "created_at": datetime.now(timezone.utc),
    }

    cases.insert_one(case)
    case.pop("_id", None)
    return jsonify(case), 201


@app.get("/api/cases")
def list_cases():
    results = list(cases.find({}, {"_id": 0}).sort("created_at", -1))
    return jsonify(results)


@app.get("/api/cases/<case_id>")
def get_case(case_id):
    case = cases.find_one({"case_id": case_id}, {"_id": 0})

    if case is None:
        return jsonify({"error": "Case not found"}), 404

    return jsonify(case)


@app.get("/api/hospitals")
def list_hospitals():
    results = list(hospitals.find({}, {"_id": 0}))
    return jsonify(results)


@app.get("/api/ambulances")
def list_ambulances():
    results = list(ambulances.find({}, {"_id": 0}))
    return jsonify(results)

@app.patch("/api/cases/<case_id>/assign")
def assign_case(case_id):
    data = request.get_json(silent=True) or {}
    ambulance_id = data.get("ambulance_id")
    hospital_id = data.get("hospital_id")

    if not ambulance_id:
        return jsonify({"error": "ambulance_id is required"}), 400

    case = cases.find_one({"case_id": case_id})
    if case is None:
        return jsonify({"error": "Case not found"}), 404

    ambulance = ambulances.find_one({
        "ambulance_id": ambulance_id,
        "status": "available",
    })
    if ambulance is None:
        return jsonify({"error": "Available ambulance not found"}), 404

    update = {
        "ambulance_id": ambulance_id,
        "case_status": "assigned",
        "route_stage": "Ambulance assigned",
    }

    if hospital_id:
        hospital = hospitals.find_one({"hospital_id": hospital_id})
        if hospital is None:
            return jsonify({"error": "Hospital not found"}), 404
        update["hospital_id"] = hospital_id

    cases.update_one({"case_id": case_id}, {"$set": update})
    ambulances.update_one(
        {"ambulance_id": ambulance_id},
        {"$set": {"status": "assigned", "case_id": case_id}},
    )

    updated_case = cases.find_one({"case_id": case_id}, {"_id": 0})
    return jsonify(updated_case)


@app.patch("/api/cases/<case_id>/location")
def update_location(case_id):
    data = request.get_json(silent=True) or {}
    latitude = data.get("latitude")
    longitude = data.get("longitude")

    if latitude is None or longitude is None:
        return jsonify({"error": "latitude and longitude are required"}), 400

    result = cases.update_one(
        {"case_id": case_id},
        {"$set": {
            "ambulance_latitude": latitude,
            "ambulance_longitude": longitude,
        }},
    )

    if result.matched_count == 0:
        return jsonify({"error": "Case not found"}), 404

    updated_case = cases.find_one({"case_id": case_id}, {"_id": 0})
    return jsonify(updated_case)


@app.patch("/api/cases/<case_id>/status")
def update_case_status(case_id):
    data = request.get_json(silent=True) or {}
    status = data.get("case_status")

    allowed_statuses = [
        "requested",
        "assigned",
        "en_route",
        "at_scene",
        "transporting",
        "arrived",
        "completed",
    ]

    if status not in allowed_statuses:
        return jsonify({
            "error": "Invalid case_status",
            "allowed_statuses": allowed_statuses,
        }), 400

    result = cases.update_one(
        {"case_id": case_id},
        {"$set": {"case_status": status}},
    )

    if result.matched_count == 0:
        return jsonify({"error": "Case not found"}), 404

    updated_case = cases.find_one({"case_id": case_id}, {"_id": 0})
    return jsonify(updated_case)
if __name__ == "__main__":
    seed_demo_data()
    app.run(debug=True)