from flask import Flask, request, jsonify
from core.knowledge_manager import knowledge_manager

app = Flask(__name__)


@app.route("/")
def home():
    return jsonify({
        "app": "T-ZONE Admin API",
        "status": "running"
    })


@app.route("/api/<service>/faqs", methods=["GET"])
def list_faqs(service):
    return jsonify(knowledge_manager.list_faqs(service))


@app.route("/api/<service>/faqs/<faq_id>", methods=["GET"])
def get_faq(service, faq_id):
    faq = knowledge_manager.get_faq(service, faq_id)

    if not faq:
        return jsonify({"error": "FAQ not found"}), 404

    return jsonify(faq)


@app.route("/api/<service>/faqs", methods=["POST"])
def save_faq(service):
    data = request.json

    required = ["id", "title_ar", "title_en", "body_ar", "body_en"]

    for field in required:
        if field not in data:
            return jsonify({"error": f"Missing field: {field}"}), 400

    faq = knowledge_manager.save_faq(service, data)

    return jsonify({
        "message": "FAQ saved",
        "faq": faq
    })


@app.route("/api/<service>/faqs/<faq_id>", methods=["DELETE"])
def delete_faq(service, faq_id):
    deleted = knowledge_manager.delete_faq(service, faq_id)

    if not deleted:
        return jsonify({"error": "FAQ not found"}), 404

    return jsonify({"message": "FAQ deleted"})


if __name__ == "__main__":
    app.run(debug=True, port=5000)