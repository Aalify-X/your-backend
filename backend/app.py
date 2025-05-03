from flask import Flask, session, redirect, url_for, request, jsonify, render_template
from flask_cors import CORS
from functools import wraps
import os
from docx import Document
import requests
import io
from io import BytesIO
from werkzeug.utils import secure_filename
from dotenv import load_dotenv
from datetime import timedelta
import signal
from contextlib import contextmanager
from PyPDF2 import PdfReader

load_dotenv()

# Get absolute paths to templates and static folders
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_DIR = os.path.join(BASE_DIR, '../frontend/templates')
STATIC_DIR = os.path.join(BASE_DIR, '../frontend/static')

app = Flask(__name__, 
            template_folder=TEMPLATE_DIR, 
            static_folder=STATIC_DIR)

# Use a fixed secret key for session persistence
app.secret_key = os.getenv('FLASK_SECRET_KEY', 'NSjUyKL1$8N*@(i')

# Configure session to be permanent
app.config['SESSION_PERMANENT'] = True
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)  # Session lasts 30 days

# Configure CORS
CORS(app, supports_credentials=True)

# Set up session cookie settings
app.config.update(
    SESSION_COOKIE_SECURE=False,  # Allow HTTP in development
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax'
)

# Set the port for Render
app.config['PORT'] = int(os.getenv('PORT', 5000))

# ================== WHOP AUTHENTICATION ==================

def whop_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # Skip verification in development
        if os.getenv('FLASK_ENV') == 'development':
            return f(*args, **kwargs)
            
        # Check if already verified in session
        if 'whop_verified' in session:
            return f(*args, **kwargs)
            
        # Check for token in Authorization header
        token = request.headers.get('Authorization')
        if token:
            try:
                if verify_whop_token(token):
                    return f(*args, **kwargs)
            except Exception:
                pass
                
        # Not verified - redirect to verification
        return redirect(url_for('whop_verification'))
        
    return decorated_function

def verify_whop_token(token):
    """Verify WHOP token with their API"""
    if not token.startswith('Bearer '):
        token = f'Bearer {token}'
    
    response = requests.get(
        "https://api.whop.com/api/v2/me",
        headers={"Authorization": token}
    )
    
    if response.status_code == 200:
        session['whop_verified'] = True
        session['whop_user'] = response.json()
        return True
    return False

# ================== ROUTES ==================

@app.route('/')
def index():
    if 'whop_verified' not in session:
        return redirect(url_for('whop_verification'))
    return render_template('index.html')

@app.route('/verify')
def whop_verification():
    """WHOP verification page"""
    app.jinja_env.cache = {}
    return render_template('whop.html')

@app.route('/api/verify_whop', methods=['POST'])
def api_verify_whop():
    """Endpoint for WHOP widget to verify token"""
    token = request.headers.get('Authorization', '').replace('Bearer ', '')
    
    if not token:
        return jsonify({'success': False, 'message': 'No token provided'}), 401
    
    try:
        if verify_whop_token(token):
            return jsonify({'success': True})
        return jsonify({'success': False, 'message': 'Invalid token'}), 401
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('whop_verification'))

# ================== PROTECTED ROUTES ==================

@app.route('/dashboard')
@whop_required
def dashboard():
    return render_template('index.html')

@app.route('/digitalplanner')
@whop_required
def digital_planner():
    return render_template('digital_planner.html')

@app.route('/whiteboard')
@whop_required
def whiteboard():
    return render_template('whiteboard.html')

@app.route('/flashcards')
@whop_required
def flashcards():
    return render_template('flashcards.html')

@app.route('/pdf_tools')
@whop_required
def pdf_tools():
    return render_template('pdf_document_intelligence.html')

# ================== DOCUMENT PROCESSING ==================

@app.route('/api/process_document', methods=['POST'])
@whop_required
def process_document():
    try:
        if 'file' not in request.files:
            return jsonify({"error": "No file uploaded"}), 400

        file = request.files['file']
        filename = secure_filename(file.filename)

        if not filename:
            return jsonify({"error": "Invalid file"}), 400

        summary_length = int(request.form.get('summary_length', 35))
        question_count = int(request.form.get('question_count', 10))

        try:
            if filename.lower().endswith('.pdf'):
                text = extract_text_from_pdf(file)
            elif filename.lower().endswith(('.doc', '.docx')):
                text = extract_text_from_word(file)
            else:
                return jsonify({"error": "Unsupported file type"}), 400

            if not text or len(text.strip()) < 100:
                return jsonify({"error": "No readable text in file"}), 400

            # Process text in smaller chunks
            chunk_size = 5000
            chunks = [text[i:i+chunk_size] for i in range(0, len(text), chunk_size)]
            summaries = []
            questions = []

            for chunk in chunks:
                try:
                    with timeout(15):
                        summary = generate_summary(chunk)
                        if summary:
                            summaries.append(summary)
                        
                        chunk_questions = generate_questions(chunk)
                        if chunk_questions:
                            questions.extend(chunk_questions)
                except TimeoutException:
                    continue
                except Exception as e:
                    continue

            final_summary = " ".join(summaries).strip()
            
            return jsonify({
                "summary": final_summary or "Failed to generate summary",
                "questions": questions or ["Failed to generate questions"],
                "status": "success"
            })

        except Exception as e:
            return jsonify({"error": f"Failed to process document: {str(e)}"}), 500

    except ValueError as e:
        return jsonify({"error": "Invalid input parameters"}), 400
    except Exception as e:
        return jsonify({"error": f"Internal server error: {str(e)}"}), 500

# [Keep all your existing helper functions...]

if __name__ == '__main__':
    port = app.config['PORT']
    app.run(host='0.0.0.0', port=port, debug=True)
