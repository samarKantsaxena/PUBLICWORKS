from flask import Flask, request, jsonify
from flask_cors import CORS
from supabase import create_client
from dotenv import load_dotenv
import os
import uuid

load_dotenv()

app = Flask(__name__)
CORS(app)

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_ANON_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise ValueError("Missing SUPABASE_URL or SUPABASE_ANON_KEY in environment")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

def get_user_id_from_token(token):
    """Extract user ID from Bearer token using Supabase."""
    if not token:
        return None
    try:
        # get_user works with anon key for the user's own token
        user = supabase.auth.get_user(token)
        if user and user.user:
            return user.user.id
    except Exception as e:
        print(f"Auth error: {e}")
    return None

@app.route('/upload-photo', methods=['POST'])
def upload_photo():
    try:
        if 'photo' not in request.files:
            return jsonify({"error": "No photo"}), 400
        file = request.files['photo']
        ext = file.filename.split('.')[-1]
        filename = f"{uuid.uuid4()}.{ext}"
        file_bytes = file.read()
        supabase.storage.from_('issue-photos').upload(filename, file_bytes, file_options={"content-type": file.mimetype})
        public_url = supabase.storage.from_('issue-photos').get_public_url(filename)
        return jsonify({"url": public_url})
    except Exception as e:
        print(f"Upload error: {e}")
        return jsonify({"error": str(e)}), 500

def categorize_issue(title, description, image_url=None):
    if not GROQ_API_KEY:
        return "other"
    try:
        from groq import Groq
        client = Groq(api_key=GROQ_API_KEY)
        prompt = f"Classify into one of: pothole, broken streetlight, garbage dumping, drainage, water leakage, electrical hazard, road damage, sidewalk issue, other.\nTitle: {title}\nDescription: {description}"
        if image_url:
            completion = client.chat.completions.create(
                model="meta-llama/llama-4-scout-17b-16e-instruct",
                messages=[{"role": "user", "content": [{"type": "text", "text": prompt}, {"type": "image_url", "image_url": {"url": image_url}}]}],
                temperature=0.3
            )
        else:
            completion = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3
            )
        category = completion.choices[0].message.content.strip().lower()
        allowed = ["pothole", "broken streetlight", "garbage dumping", "drainage", "water leakage", "electrical hazard", "road damage", "sidewalk issue", "other"]
        return category if category in allowed else "other"
    except Exception as e:
        print(f"Groq error: {e}")
        return "other"

@app.route('/report-issue', methods=['POST'])
def report_issue():
    try:
        data = request.get_json()
        title = data.get('title')
        description = data.get('description', '')
        lat = data.get('latitude')
        lng = data.get('longitude')
        image_url = data.get('image_url')
        if not all([title, lat, lng]):
            return jsonify({"error": "Title and location required"}), 400
        auth_header = request.headers.get('Authorization', '')
        token = auth_header.replace('Bearer ', '') if auth_header else None
        user_id = get_user_id_from_token(token)
        category = categorize_issue(title, description, image_url)
        issue_data = {
            "title": title,
            "description": description,
            "latitude": lat,
            "longitude": lng,
            "image_url": image_url,
            "category": category,
            "upvotes": 0,
            "status": "reported"
        }
        if user_id:
            issue_data["user_id"] = user_id
        result = supabase.table('issues').insert(issue_data).execute()
        return jsonify({"success": True, "id": result.data[0]['id']})
    except Exception as e:
        print(f"Report issue error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/issues', methods=['GET'])
def get_issues():
    try:
        issues = supabase.table('issues').select('*').order('created_at', desc=True).execute()
        return jsonify(issues.data)
    except Exception as e:
        print(f"Get issues error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/issues/<int:issue_id>/claim', methods=['POST'])
def claim_issue(issue_id):
    auth_header = request.headers.get('Authorization', '')
    print(f"Claim - Authorization header: {auth_header}")
    token = auth_header.replace('Bearer ', '') if auth_header else None
    user_id = get_user_id_from_token(token)
    print(f"Claim - user_id from token: {user_id}")
    if not user_id:
        return jsonify({"error": "Unauthorized"}), 401
    profile = supabase.table('profiles').select('role').eq('id', user_id).execute()
    if not profile.data or profile.data[0]['role'] != 'ngo':
        return jsonify({"error": "Only NGOs can claim issues"}), 403
    supabase.table('issues').update({"ngo_claimed_by": user_id, "status": "in_progress"}).eq('id', issue_id).execute()
    return jsonify({"success": True})

@app.route('/issues/<int:issue_id>/resolve', methods=['POST'])
def resolve_issue(issue_id):
    auth_header = request.headers.get('Authorization', '')
    token = auth_header.replace('Bearer ', '') if auth_header else None
    user_id = get_user_id_from_token(token)
    if not user_id:
        return jsonify({"error": "Unauthorized"}), 401
    issue = supabase.table('issues').select('ngo_claimed_by').eq('id', issue_id).execute()
    if not issue.data or issue.data[0]['ngo_claimed_by'] != user_id:
        return jsonify({"error": "You have not claimed this issue"}), 403
    supabase.table('issues').update({"status": "resolved"}).eq('id', issue_id).execute()
    return jsonify({"success": True})

@app.route('/issues/<int:issue_id>/upvote', methods=['POST'])
def upvote_issue(issue_id):
    try:
        issue = supabase.table('issues').select('upvotes').eq('id', issue_id).execute()
        if not issue.data:
            return jsonify({"error": "Issue not found"}), 404
        new_upvotes = issue.data[0]['upvotes'] + 1
        supabase.table('issues').update({"upvotes": new_upvotes}).eq('id', issue_id).execute()
        return jsonify({"success": True, "upvotes": new_upvotes})
    except Exception as e:
        print(f"Upvote error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/issues/<int:issue_id>/comment', methods=['POST'])
def add_comment(issue_id):
    auth_header = request.headers.get('Authorization', '')
    token = auth_header.replace('Bearer ', '') if auth_header else None
    user_id = get_user_id_from_token(token)
    if not user_id:
        return jsonify({"error": "Unauthorized"}), 401
    comment_text = request.json.get('comment')
    if not comment_text:
        return jsonify({"error": "Comment required"}), 400
    supabase.table('issue_comments').insert({
        "issue_id": issue_id,
        "user_id": user_id,
        "comment": comment_text
    }).execute()
    return jsonify({"success": True})

@app.route('/issues/<int:issue_id>/comments', methods=['GET'])
def get_comments(issue_id):
    try:
        comments = supabase.table('issue_comments').select('*').eq('issue_id', issue_id).order('created_at', asc=True).execute()
        return jsonify(comments.data)
    except Exception as e:
        print(f"Get comments error: {e}")
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)