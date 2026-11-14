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


def calculate_demo_risk(symptoms):
    """Keyword based demo flag; it is not a medical assessment."""
    if isinstance(symptoms, str):
        symptoms = [symptoms]
    text = " ".join(str(item).lower() for item in symptoms)
    if any(word in text for word in ("chest pain", "unconscious", "severe bleeding", "not breathing")):
        return "High (demo)"
    if any(word in text for word in ("breath", "injury", "accident", "pain")):
        return "Moderate (demo)"
    return "Low (demo)"


def hospital_available(hospital):
    return hospital.get("accepting", hospital.get("available", False)) is True


def find_hospital(identifier):
    return hospitals.find_one({"$or": [{"hospital_id": identifier}, {"name": identifier}]})


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
        "risk_level": calculate_demo_risk(data["symptoms"]),
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
        "signal_priority": "inactive",
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
        hospital = hospitals.find_one({
            "$or": [
                {"hospital_id": hospital_id},
                {"name": hospital_id},
            ]
        })

        if hospital is None:
            return jsonify({"error": "Hospital not found"}), 404

        # Store the stable ID when present; otherwise use the hospital name.
        update["hospital_id"] = hospital.get("hospital_id") or hospital["name"]

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

    if not isinstance(latitude, (int, float)) or not isinstance(longitude, (int, float)):
        return jsonify({"error": "coordinates must be numbers"}), 400

    if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
        return jsonify({"error": "coordinates are outside valid ranges"}), 400

    case = cases.find_one({"case_id": case_id})
    if case is None:
        return jsonify({"error": "Case not found"}), 404

    cases.update_one(
        {"case_id": case_id},
        {"$set": {
            "ambulance_latitude": latitude,
            "ambulance_longitude": longitude,
        }},
    )

    # Keep the ambulance's own location record in sync as well.
    if case.get("ambulance_id"):
        ambulances.update_one(
            {"ambulance_id": case["ambulance_id"]},
            {"$set": {
                "latitude": latitude,
                "longitude": longitude,
            }},
        )

    updated_case = cases.find_one({"case_id": case_id}, {"_id": 0})
    return jsonify(updated_case)
@app.patch("/api/cases/<case_id>/status")
def update_case_status(case_id):
    data = request.get_json(silent=True) or {}
    status = data.get("case_status")

    route_stages = {
        "requested": "Waiting for ambulance",
        "assigned": "Ambulance assigned",
        "en_route": "Ambulance en route",
        "at_scene": "Ambulance at pickup",
        "transporting": "Transporting to hospital",
        "arrived": "Arrived at hospital",
        "completed": "Case completed",
    }

    if status not in route_stages:
        return jsonify({
            "error": "Invalid case_status",
            "allowed_statuses": list(route_stages.keys()),
        }), 400

    result = cases.update_one(
        {"case_id": case_id},
        {"$set": {
            "case_status": status,
            "route_stage": route_stages[status],
        }},
    )

    if result.matched_count == 0:
        return jsonify({"error": "Case not found"}), 404

    if status == "completed":
        case = cases.find_one({"case_id": case_id})
        if case and case.get("ambulance_id"):
            ambulances.update_one(
                {"ambulance_id": case["ambulance_id"]},
                {"$set": {"status": "available", "case_id": None}},
            )

    updated_case = cases.find_one({"case_id": case_id}, {"_id": 0})
    return jsonify(updated_case)
@app.get("/api/cases/<case_id>/recommend-hospital")
def recommend_hospital(case_id):
    case = cases.find_one({"case_id": case_id}, {"_id": 0})

    if case is None:
        return jsonify({"error": "Case not found"}), 404

    symptoms = case.get("symptoms", [])
    if isinstance(symptoms, str):
        symptoms = [symptoms]
    symptoms = [str(symptom).lower() for symptom in symptoms]

    # Simple project demo rule, not medical diagnosis.
    required_facility = "general"

    if any("chest" in symptom or "heart" in symptom for symptom in symptoms):
        required_facility = "cardiac"
    elif any("injury" in symptom or "accident" in symptom for symptom in symptoms):
        required_facility = "trauma"
    elif any("breath" in symptom or "respiratory" in symptom for symptom in symptoms):
        required_facility = "respiratory"

    candidates = list(hospitals.find(
        {"facilities": required_facility},
        {"_id": 0},
    ))

    # Accept either field name used by the two demo data formats.
    candidates = [
        hospital for hospital in candidates
        if hospital.get("accepting", hospital.get("available", False))
    ]

    if not candidates:
        return jsonify({
            "case_id": case_id,
            "required_facility": required_facility,
            "recommendation": None,
            "message": "No accepting demo hospital lists this facility.",
            "note": "This is a demo matching rule, not medical advice.",
        }), 200

    candidates.sort(
        key=lambda hospital: hospital.get("demo_eta_minutes", 9999)
    )
    selected = candidates[0]
    selected_id = selected.get("hospital_id") or selected.get("name")

    cases.update_one(
        {"case_id": case_id},
        {"$set": {"hospital_id": selected_id}},
    )

    return jsonify({
        "case_id": case_id,
        "required_facility": required_facility,
        "recommendation": selected,
        "note": "This is a demo matching rule, not medical advice.",
    })
@app.patch("/api/cases/<case_id>/route")
def update_route(case_id):
    data = request.get_json(silent=True) or {}

    traffic_delays = {
        "Clear": 0,
        "Moderate": 5,
        "Heavy": 12,
    }

    traffic = data.get("traffic_level", "Clear")
    if traffic not in traffic_delays:
        return jsonify({
            "error": "traffic_level must be Clear, Moderate, or Heavy"
        }), 400

    base_eta = data.get("base_eta_minutes", 10)
    if not isinstance(base_eta, (int, float)) or base_eta < 0:
        return jsonify({"error": "base_eta_minutes must be a non-negative number"}), 400

    case = cases.find_one({"case_id": case_id})
    if case is None:
        return jsonify({"error": "Case not found"}), 404

    update = {
        "traffic_level": traffic,
        "eta_minutes": int(base_eta + traffic_delays[traffic]),
    }

    if "route_stage" in data:
        update["route_stage"] = data["route_stage"]

    if "signal_priority" in data:
        if data["signal_priority"] not in ["active", "inactive"]:
            return jsonify({
                "error": "signal_priority must be active or inactive"
            }), 400
        update["signal_priority"] = data["signal_priority"]

    cases.update_one({"case_id": case_id}, {"$set": update})
    updated_case = cases.find_one({"case_id": case_id}, {"_id": 0})
    return jsonify(updated_case)
if __name__ == "__main__":
    seed_demo_data()
    app.run(debug=True)
