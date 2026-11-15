import os
import requests
from pymongo import MongoClient,ReturnDocument
from datetime import datetime, timezone
from uuid import uuid4
from flask import Flask, jsonify, request, render_template


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
def generate_case_id():
    counter = db["counters"].find_one_and_update(
        {"_id": "case_id"},
        {"$inc": {"sequence": 1}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return f"SEVA-{counter['sequence']:04d}"

def hospital_available(hospital):
    return hospital.get("accepting", hospital.get("available", False)) is True


def find_hospital(identifier):
    return hospitals.find_one({"$or": [{"hospital_id": identifier}, {"name": identifier}]})


def seed_demo_data():
    hospital_records = [
        {
            "hospital_id": "H001",
            "name": "A.J. Hospital & Research Centre",
            "latitude": 12.903178,
            "longitude": 74.845365,
            "facilities": ["general", "emergency", "trauma", "cardiac", "respiratory", "icu"],
            "accepting": True,
            "available": True,
            "capacity": "Demo available",
            "demo_eta_minutes": 12,
        },
        {
            "hospital_id": "H002",
            "name": "Father Muller Medical College Hospital",
            "latitude": 12.86721,
            "longitude": 74.86075,
            "facilities": ["general", "emergency", "trauma", "respiratory", "icu"],
            "accepting": True,
            "available": True,
            "capacity": "Demo available",
            "demo_eta_minutes": 8,
        },
        {
            "hospital_id": "H003",
            "name": "KMC Hospital, Mangaluru",
            "latitude": 12.8719,
            "longitude": 74.8483,
            "facilities": ["general", "emergency", "trauma", "cardiac", "icu"],
            "accepting": True,
            "available": True,
            "capacity": "Demo available",
            "demo_eta_minutes": 6,
        },
    ]
    old_hospital_names = {
        "H001": ["Central General Hospital", "Sahyadri Demo Hospital"],
        "H002": ["Southside Medical Centre", "City General Demo Hospital"],
        "H003": ["Community Demo Clinic"],
    }

    # Update known demo records as well as inserting missing ones. The old
    # seed only ran for an empty collection, leaving earlier city data in MongoDB.
    for hospital in hospital_records:
        match = hospitals.find_one({
            "$or": [
                {"hospital_id": hospital["hospital_id"]},
                {"name": {"$in": old_hospital_names[hospital["hospital_id"]] + [hospital["name"]]}},
            ]
        })
        if match:
            hospitals.update_one({"_id": match["_id"]}, {"$set": hospital})
        else:
            hospitals.insert_one(hospital)

    ambulance_records = [
        {"ambulance_id": "A001", "name": "Mangaluru Demo Ambulance 1", "latitude": 12.872, "longitude": 74.849},
        {"ambulance_id": "A002", "name": "Mangaluru Demo Ambulance 2", "latitude": 12.879, "longitude": 74.856},
    ]
    for ambulance in ambulance_records:
        ambulances.update_one(
            {"ambulance_id": ambulance["ambulance_id"]},
            {
                "$set": {
                    "name": ambulance["name"],
                    "latitude": ambulance["latitude"],
                    "longitude": ambulance["longitude"],
                },
                "$setOnInsert": {"status": "available", "case_id": None},
            },
            upsert=True,
        )

    # Keep the current walkthrough case in the same Mangaluru demo area.
    # Hospital recommendation should be run again after the seed names change.
    cases.update_one(
        {"case_id": "SEVA-0002"},
        {
            "$set": {
                "pickup_latitude": 12.879,
                "pickup_longitude": 74.856,
                "ambulance_latitude": 12.872,
                "ambulance_longitude": 74.849,
            },
            "$unset": {"hospital_id": ""},
        },
    )


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

def classify_risk(symptoms):
    if isinstance(symptoms, str):
        symptoms = [symptoms]

    symptoms_text = " ".join(symptoms).lower()

    high_risk_terms = [
        "breathing difficulty",
        "unconscious",
        "severe bleeding",
        "chest pain",
    ]
    medium_risk_terms = [
        "high fever",
        "serious injury",
        "persistent vomiting",
    ]

    if any(term in symptoms_text for term in high_risk_terms):
        return "High"
    if any(term in symptoms_text for term in medium_risk_terms):
        return "Medium"
    return "Low"
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
        "case_id": generate_case_id(),
        "patient_name": data["patient_name"],
        "caller_name": data["caller_name"],
        "symptoms": data["symptoms"],
       "risk_level": classify_risk(data["symptoms"]),
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
            "signal_priority": (
                "active" if status in {"en_route", "transporting"} else "inactive"
            ),
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
@app.get("/api/routes")
def list_api_routes():
    routes = []

    for rule in app.url_map.iter_rules():
        path = str(rule)
        if path.startswith("/api/"):
            methods = sorted(rule.methods - {"HEAD", "OPTIONS"})
            routes.append({
                "path": path,
                "methods": methods,
            })

    return jsonify(routes)
@app.get("/api/cases/<case_id>/live-route")
def get_live_route(case_id):
    api_key = os.getenv("ORS_API_KEY")
    if not api_key:
        return jsonify({"error": "ORS_API_KEY is not set"}), 500

    case = cases.find_one({"case_id": case_id})
    if case is None:
        return jsonify({"error": "Case not found"}), 404

    ambulance_id = case.get("ambulance_id")
    ambulance = (
        ambulances.find_one({"ambulance_id": ambulance_id})
        if ambulance_id else None
    )

    # Prefer the latest coordinates saved on the case.
    origin_lat = case.get("ambulance_latitude")
    origin_lon = case.get("ambulance_longitude")

    # If the case has no location yet, use the ambulance record.
    if origin_lat is None or origin_lon is None:
        origin_lat = (ambulance or {}).get("latitude")
        origin_lon = (ambulance or {}).get("longitude")

    destination_type = request.args.get("destination", "hospital")

    if destination_type == "scene":
        destination_lat = case.get("pickup_latitude")
        destination_lon = case.get("pickup_longitude")

    elif destination_type == "hospital":
        hospital_id = case.get("hospital_id")
        if not hospital_id:
            return jsonify({"error": "Recommend or assign a hospital first"}), 400

        hospital = find_hospital(hospital_id)
        if hospital is None:
            return jsonify({"error": "Hospital record not found"}), 404

        destination_lat = hospital.get("latitude")
        destination_lon = hospital.get("longitude")

    else:
        return jsonify({
            "error": "destination must be scene or hospital"
        }), 400

    if any(value is None for value in (
        origin_lat, origin_lon, destination_lat, destination_lon
    )):
        return jsonify({
            "error": "Ambulance and selected destination need latitude and longitude"
        }), 400

    url = (
        "https://api.heigit.org/"
        "openrouteservice/v2/directions/driving-car/geojson"
    )
    headers = {
        "Authorization": api_key,
        "Content-Type": "application/json",
    }
    body = {
        # ORS expects [longitude, latitude].
        "coordinates": [
            [origin_lon, origin_lat],
            [destination_lon, destination_lat],
        ],
        "instructions": False,
    }

    try:
        response = requests.post(url, headers=headers, json=body, timeout=20)
        response.raise_for_status()
        ors_data = response.json()
    except requests.RequestException as error:
        details = error.response.text if error.response is not None else str(error)
        return jsonify({
            "error": "ORS route request failed",
            "details": details,
        }), 502

    features = ors_data.get("features", [])
    if not features:
        return jsonify({"error": "ORS returned no route"}), 502

    route = features[0]
    summary = route.get("properties", {}).get("summary", {})
    duration_seconds = summary.get("duration")
    distance_meters = summary.get("distance")

    if duration_seconds is None or distance_meters is None:
        return jsonify({"error": "ORS response is missing route distance or duration"}), 502

    update = {
        "eta_minutes": round(duration_seconds / 60),
        "traffic_level": "Not provided by ORS",
        "traffic_delay_minutes": 0,
        "route_distance_meters": distance_meters,
        "route_geometry": route.get("geometry"),
        "route_source": "ORS route estimate; no live traffic",
        "route_destination": destination_type,
    }

    cases.update_one({"case_id": case_id}, {"$set": update})

    return jsonify({
        "case_id": case_id,
        **update,
    })
@app.get("/ambulance")
def ambulance_page():
    return render_template("ambulance.html")
if __name__ == "__main__":
    seed_demo_data()
    app.run(debug=True)
