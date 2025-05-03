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

# Configure paths
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_DIR = os.path.join(BASE_DIR, '../frontend/templates')
STATIC_DIR = os.path.join(BASE_DIR, '../frontend/static')

app = Flask(__name__,
            template_folder=TEMPLATE_DIR,
            static_folder=STATIC_DIR)

# App configuration
app.secret_key = os.getenv('FLASK_SECRET_KEY', 'default-secret-key-12345')
app.config['SESSION_PERMANENT'] = True
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)
app.config['PORT'] = int(os.getenv('PORT', 10000))  # Render default port

# CORS and session settings
CORS(app, supports_credentials=True)
app.config.update(
    SESSION_COOKIE_SECURE=os.getenv('FLASK_ENV') == 'production',
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax'
)

# ================== AUTH HELPERS ==================

class TimeoutException(Exception):
    pass

def timeout_handler(signum, frame):
    raise TimeoutException("Operation timed out")

@contextmanager
def timeout(seconds):
    signal.signal(signal.SIGALRM, timeout_handler)
    signal.alarm(seconds)
    try:
        yield
    except TimeoutException:
        raise
    finally:
        signal.alarm(0)

def whop_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if os.getenv('FLASK_ENV') == 'development':
            return f(*args, **kwargs)
            
        if 'whop_verified' in session:
            return f(*args, **kwargs)
            
        token = request.headers.get('Authorization')
        if token and verify_whop_token(token):
            return f(*args, **kwargs)
            
        return redirect(url_for('verify'))
    return decorated_function

def verify_whop_token(token):
    if not token.startswith('Bearer '):
        token = f'Bearer {token}'
    
    try:
        response = requests.get(
            "https://api.whop.com/api/v2/me",
            headers={"Authorization": token},
            timeout=5
        )
        if response.status_code == 200:
            session['whop_verified'] = True
            session['whop_user'] = response.json()
            return True
    except requests.exceptions.RequestException:
        pass
    return False

# ================== ROUTES ==================

@app.route('/')
def home():
    if 'whop_verified' in session:
        return redirect(url_for('dashboard'))
    return redirect(url_for('verify'))

@app.route('/verify')
def verify():
    app.jinja_env.cache = {}
    return render_template('whop.html')

@app.route('/dashboard')
@whop_required
def dashboard():
    return render_template('index.html')

@app.route('/api/verify_whop', methods=['POST'])
def api_verify_whop():
    token = request.headers.get('Authorization', '').replace('Bearer ', '')
    
    if not token:
        return jsonify({'success': False, 'message': 'No token provided'}), 401
    
    if verify_whop_token(token):
        return jsonify({
            'success': True,
            'redirect': url_for('dashboard')
        })
    return jsonify({'success': False, 'message': 'Invalid token'}), 401

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('verify'))

# ================== PROTECTED FEATURES ==================

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

            # Process text in chunks
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
                except Exception:
                    continue

            final_summary = " ".join(summaries).strip()
            
            return jsonify({
                "summary": final_summary or "No summary generated",
                "questions": questions or ["No questions generated"],
                "status": "success"
            })

        except Exception as e:
            return jsonify({"error": f"Failed to process document: {str(e)}"}), 500

    except ValueError:
        return jsonify({"error": "Invalid input parameters"}), 400
    except Exception as e:
        return jsonify({"error": f"Internal server error: {str(e)}"}), 500

# ================== HELPER FUNCTIONS ==================

def extract_text_from_pdf(file):
    try:
        try:
            pdf_reader = PdfReader(file)
        except Exception:
            pdf_reader = PdfReader(BytesIO(file.read()))

        text = ""
        page_count = len(pdf_reader.pages)
        batch_size = 5
        
        for batch_start in range(0, page_count, batch_size):
            batch_end = min(batch_start + batch_size, page_count)
            
            for page_num in range(batch_start, batch_end):
                try:
                    page_text = pdf_reader.pages[page_num].extract_text()
                    if page_text:
                        text += page_text.strip() + "\n"
                except Exception:
                    continue

        return text.strip() if text.strip() else "No readable text found in PDF"
        
    except Exception as e:
        raise Exception(f"PDF extraction error: {str(e)}")

def extract_text_from_word(file):
    try:
        with timeout(30):
            doc = Document(io.BytesIO(file.read()))
            text = "\n".join([para.text for para in doc.paragraphs])
            return text.strip() if text.strip() else "No readable text found in Word document"
    except TimeoutException:
        raise Exception("Word processing timed out")
    except Exception as e:
        raise Exception(f"Word extraction error: {str(e)}")

def query_openrouter(prompt):
    try:
        OPENROUTER_API_KEY = os.getenv('OPENROUTER_API_KEY')
        if not OPENROUTER_API_KEY:
            raise ValueError("OpenRouter API key not set")

        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://your-webapp.onrender.com",
                "X-Title": "Progrify PDF Summarizer"
            },
            json={
                "model": "anthropic/claude-3-sonnet-20240229",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.7,
                "max_tokens": 1000
            },
            timeout=30
        )

        if response.status_code != 200:
            error_msg = f"OpenRouter API error: {response.status_code}"
            if response.text:
                error_msg += f" - {response.text[:200]}"
            raise Exception(error_msg)

        result = response.json()
        if 'choices' in result and len(result['choices']) > 0:
            return result['choices'][0]['message']['content']
        return None

    except requests.exceptions.RequestException as e:
        raise Exception(f"Network error: {str(e)}")
    except Exception as e:
        raise Exception(f"API error: {str(e)}")

def generate_summary(text):
    try:
        prompt = f"Write a concise summary of the following text:\n\n{text[:15000]}"
        result = query_openrouter(prompt)
        return result or "Failed to generate summary"
    except Exception as e:
        raise Exception(f"Summary generation error: {str(e)}")

def generate_questions(text):
    try:
        prompt = f"""Generate exam-style questions with answers based on:
        {text[:15000]}
        Format each as: Q: [question]\nA: [answer]"""
        
        result = query_openrouter(prompt)
        if not result:
            return []
            
        questions = []
        current_q = None
        
        for line in result.split('\n'):
            line = line.strip()
            if not line:
                continue
            if line.startswith('Q:'):
                if current_q:
                    questions.append(current_q)
                current_q = {"question": line[2:].strip(), "answer": ""}
            elif line.startswith('A:') and current_q:
                current_q["answer"] = line[2:].strip()
                questions.append(current_q)
                current_q = None
        
        if current_q:
            questions.append(current_q)
            
        return questions
        
    except Exception as e:
        raise Exception(f"Question generation error: {str(e)}")

if __name__ == '__main__':
    port = app.config['PORT']
    app.run(host='0.0.0.0', port=port, debug=os.getenv('FLASK_ENV') == 'development')
