from flask import Flask, session, redirect, url_for, request, jsonify, render_template
from flask_cors import CORS
from auth.routes import auth_bp
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

TEMPLATE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '../frontend/templates'))
STATIC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '../frontend/static'))

app = Flask(__name__, template_folder=TEMPLATE_DIR, static_folder=STATIC_DIR)

# Use a fixed secret key for session persistence
app.secret_key = os.getenv('FLASK_SECRET_KEY', 'NSjUyKL1$8N*@(i')

# Configure session to be permanent
app.config['SESSION_PERMANENT'] = True
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)  # Session lasts 30 days

# Configure CORS
CORS(app, origins=["https://your-frontend.onrender.com"],
     supports_credentials=True)

# Set up session cookie settings
app.config.update(
    SESSION_COOKIE_SECURE=True,  # Only send cookie over HTTPS
    SESSION_COOKIE_HTTPONLY=True,  # Prevent JavaScript access to cookie
    SESSION_COOKIE_SAMESITE='Lax'  # Allow cross-site requests
)

# Set the port for Render
app.config['PORT'] = int(os.getenv('PORT', 10000))

# Register the auth blueprint
app.register_blueprint(auth_bp)

# Error handling
@app.errorhandler(500)
def internal_server_error(e):
    return jsonify(error=str(e)), 500

# Login required decorator
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('user'):
            return redirect(url_for('auth_bp.login'))
        return f(*args, **kwargs)
    return decorated_function

# Routes
@app.route('/')
@app.route('/index')
def index():
    return render_template('index.html')

@app.route('/digital_planner')
def digital_planner():
    return render_template('digital_planner.html')

@app.route('/whiteboard')
def whiteboard():
    return render_template('whiteboard.html')

@app.route('/flashcards')
def flashcards():
    return render_template('flashcards.html')

@app.route('/pdf_tools')
def pdf_tools():
    return render_template('pdf_document_intelligence.html')

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

# Document processing
@app.route('/api/process_document', methods=['POST'])
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

            if not text or len(text.strip()) < 100:  # Check for meaningful text
                return jsonify({"error": "No readable text in file"}), 400

            # Process text in smaller chunks to prevent timeouts
            chunk_size = 5000  # Process 5000 characters at a time
            chunks = [text[i:i+chunk_size] for i in range(0, len(text), chunk_size)]
            summaries = []
            questions = []

            for chunk in chunks:
                try:
                    with timeout(15):  # 15 second timeout per chunk
                        summary = generate_summary(chunk)
                        if summary:
                            summaries.append(summary)
                        
                        chunk_questions = generate_questions(chunk)
                        if chunk_questions:
                            questions.extend(chunk_questions)
                except TimeoutException:
                    print("Processing chunk timed out")
                    continue
                except Exception as e:
                    print(f"Error processing chunk: {str(e)}")
                    continue

            final_summary = " ".join(summaries).strip()
            
            return jsonify({
                "summary": final_summary or "Failed to generate summary",
                "questions": questions or ["Failed to generate questions"],
                "status": "success"
            })

        except Exception as e:
            print(f"Processing error: {str(e)}")
            return jsonify({"error": f"Failed to process document: {str(e)}"}), 500

    except ValueError as e:
        print(f"Value error: {str(e)}")
        return jsonify({"error": "Invalid input parameters"}), 400
    except Exception as e:
        print(f"API error: {str(e)}")
        return jsonify({"error": f"Internal server error: {str(e)}"}), 500

# Helper functions
def extract_text_from_pdf(file):
    try:
        # Try to read the file directly
        try:
            pdf_reader = PdfReader(file)
        except Exception:
            # If that fails, try reading as bytes
            pdf_reader = PdfReader(BytesIO(file.read()))

        text = ""
        # Process pages in smaller batches
        page_count = len(pdf_reader.pages)
        batch_size = 5
        
        for batch_start in range(0, page_count, batch_size):
            batch_end = min(batch_start + batch_size, page_count)
            
            for page_num in range(batch_start, batch_end):
                try:
                    page_text = pdf_reader.pages[page_num].extract_text()
                    if page_text:
                        text += page_text.strip() + "\n"
                except Exception as e:
                    print(f"Error extracting text from page {page_num}: {str(e)}")
                    continue

        if not text.strip():
            return "No readable text found in PDF"
        return text.strip()
        
    except Exception as e:
        print(f"PDF extraction error: {str(e)}")
        raise

def extract_text_from_word(file):
    try:
        with timeout(30):  # 30 second timeout for Word processing
            doc = Document(io.BytesIO(file.read()))
            text = "\n".join([para.text for para in doc.paragraphs])
            if not text.strip():
                return "No readable text found in Word document"
            return text.strip()
    except TimeoutException:
        print("Word processing timed out")
        raise Exception("Word processing took too long")
    except Exception as e:
        print(f"Word extraction error: {str(e)}")
        raise

def query_openrouter(prompt):
    try:
        if not OPENROUTER_API_KEY:
            raise ValueError("OpenRouter API key is not set")

        data = {
            "model": "anthropic/claude-3-sonnet-20240229",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.7,
            "max_tokens": 1000
        }

        response = requests.post(
            OPENROUTER_URL,
            headers=OPENROUTER_HEADERS,
            json=data,
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
        print(f"Network error: {str(e)}")
        raise Exception(f"Network error: {str(e)}")
    except ValueError as e:
        print(f"Value error: {str(e)}")
        raise Exception(f"Configuration error: {str(e)}")
    except Exception as e:
        print(f"API error: {str(e)}")
        raise Exception(f"Failed to get response from OpenRouter: {str(e)}")

def generate_summary(text):
    try:
        prompt = f"Write a concise summary of the following text:\n\n{text[:15000]}"
        result = query_openrouter(prompt)
        return result or "Failed to generate summary. Please try again."
    except Exception as e:
        print(f"Summary generation error: {str(e)}")
        raise

def generate_questions(text):
    try:
        prompt = f"""Generate important exam-style questions with answers based on the following text.\nFormat each as:\nQ: [question]\nA: [answer]\n\n{text[:15000]}"""
        result = query_openrouter(prompt)
        
        if not result:
            return [{"question": "Error generating questions", "answer": "Please try again"}]

        questions = []
        current_q = None
        for line in result.split('\n'):
            line = line.strip()
            if not line:
                continue
            if line.startswith('Q:'):
                if current_q:
                    questions.append(current_q)
                q_text = line[2:].strip()
                current_q = {"question": q_text, "answer": ""}
            elif line.startswith('A:') and current_q:
                current_q["answer"] = line[2:].strip()
                questions.append(current_q)
                current_q = None
        if current_q:
            questions.append(current_q)
        return questions
    except Exception as e:
        print(f"Question generation error: {str(e)}")
        raise

# OpenRouter configuration
OPENROUTER_API_KEY = os.getenv('OPENROUTER_API_KEY')
if not OPENROUTER_API_KEY:
    raise ValueError("OpenRouter API key is not set in environment variables")

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_HEADERS = {
    "Authorization": f"Bearer {OPENROUTER_API_KEY}",
    "Content-Type": "application/json",
    "HTTP-Referer": "https://www.progrify.pro",  # Update to match your domain
    "X-Title": "Progrify PDF Summarizer"
}

# Main
if __name__ == '__main__':
    port = app.config['PORT']
    app.run(host='0.0.0.0', port=port)
