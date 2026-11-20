
from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

cases = {
    "C001": {
        "case_id": "C001",
        "patient_name": "Test Patient",
        "symptoms": "Chest pain",
        "severity": "high",
        "status": "assigned",
        "location": {
            "lat": 12.9141,
            "lng": 74.8560,
            "address": "Mangaluru Central"
        },
        "hospital": {
            "hospital_id": "H1",
            "name": "Demo Hospital",
            "lat": 12.8700,
            "lng": 74.8420
        }
    }
}

ambs = {
    "AMB-01": {
        "ambulance_id": "AMB-01",
        "assigned_case_id": "C001",
        "location": None
    }
}


@app.route("/api/ambulances/<a>", methods=["GET"])
def get_ambulance(a):
    if a not in ambs:
        return jsonify({"error": "Ambulance not found"}), 404
    return jsonify(ambs[a])


@app.route("/api/cases/<c>", methods=["GET"])
def get_case(c):
    if c not in cases:
        return jsonify({"error": "Case not found"}), 404
    return jsonify(cases[c])


@app.route("/api/ambulances/<a>/location", methods=["POST"])
def update_location(a):
    if a not in ambs:
        return jsonify({"error": "Ambulance not found"}), 404

    data = request.json
    ambs[a]["location"] = {
        "lat": data["lat"],
        "lng": data["lng"],
        "timestamp": data["timestamp"]
    }

    return jsonify({"success": True})


@app.route("/api/cases/<c>/status", methods=["PATCH"])
def update_status(c):
    if c not in cases:
        return jsonify({"error": "Case not found"}), 404

    cases[c]["status"] = request.json["status"]
    return jsonify(cases[c])


if __name__ == "__main__":
    app.run(port=5000, debug=True)
