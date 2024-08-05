import os
import base64
from flask import Flask, request, abort
from flask_cors import CORS
from werkzeug.utils import secure_filename
from dotenv import load_dotenv
import json
from datetime import datetime
import logging
from logging.handlers import RotatingFileHandler

# OpenAI
from openai import AzureOpenAI

# Line Bot SDK
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage, ImageMessage

# Load environment variables
load_dotenv()

# Initialize Flask app
app = Flask(__name__)
CORS(app)

# Constants
RESULTS_FOLDER = "analysis_results"
UPLOAD_FOLDER = "uploaded_images"
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg'}

# App configuration
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB limit

# Ensure necessary folders exist
for folder in [RESULTS_FOLDER, UPLOAD_FOLDER]:
    os.makedirs(folder, exist_ok=True)

# Initialize Azure OpenAI client
azure_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
azure_key = os.getenv("AZURE_OPENAI_KEY")
deployment_name = os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME")

client = AzureOpenAI(
    api_key=azure_key,  
    api_version="2023-05-15",
    azure_endpoint=azure_endpoint
)

# Line Bot configuration
line_bot_api = LineBotApi(os.getenv('LINE_CHANNEL_ACCESS_TOKEN'))
handler = WebhookHandler(os.getenv('LINE_CHANNEL_SECRET'))

# Configure logging
def setup_logging():
    log_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    log_file = 'app.log'
    file_handler = RotatingFileHandler(log_file, maxBytes=5*1024*1024, backupCount=3)
    file_handler.setFormatter(log_formatter)
    file_handler.setLevel(logging.INFO)
    app.logger.addHandler(file_handler)
    app.logger.setLevel(logging.INFO)

setup_logging()

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def read_prompt(file_path='prompt.txt'):
    try:
        with open(file_path, 'r', encoding='utf-8') as file:
            return file.read().strip()
    except FileNotFoundError:
        app.logger.warning(f"Prompt file {file_path} not found. Using default prompt.")
        return "Please identify the plastic material in the image and provide the corresponding plastic code."

def analyze_plastic(image_path, prompt):
    try:
        with open(image_path, "rb") as image_file:
            base64_image = base64.b64encode(image_file.read()).decode('utf-8')

        response = client.chat.completions.create(
            model=deployment_name,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{base64_image}"
                            }
                        }
                    ],
                }
            ],
            max_tokens=300,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        app.logger.error(f"Error during analysis: {str(e)}", exc_info=True)
        raise

def save_result(image_path, result):
    result_data = {
        "timestamp": datetime.now().isoformat(),
        "image_path": image_path,
        "plastic_code": result
    }
    filename = f"plastic_result_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    file_path = os.path.join(RESULTS_FOLDER, filename)
    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump(result_data, f, ensure_ascii=False, indent=4)
    app.logger.info(f"Result saved to {file_path}")
    return file_path

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    app.logger.info("Request body: " + body)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

@handler.add(MessageEvent, message=ImageMessage)
def handle_image_message(event):
    message_content = line_bot_api.get_message_content(event.message.id)
    file_path = os.path.join(app.config['UPLOAD_FOLDER'], f"{event.message.id}.jpg")
    with open(file_path, 'wb') as fd:
        for chunk in message_content.iter_content():
            fd.write(chunk)
    
    prompt = read_prompt()
    result = analyze_plastic(file_path, prompt)
    save_result(file_path, result)
    
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=result)
    )

if __name__ == '__main__':
    app.logger.info("Starting Line Bot PSD Analysis API...")
    app.logger.info(f"Azure OpenAI Endpoint: {'Set' if azure_endpoint else 'Not set'}")
    app.logger.info(f"Azure OpenAI Key: {'Set' if azure_key else 'Not set'}")
    app.logger.info(f"Azure OpenAI Deployment Name: {'Set' if deployment_name else 'Not set'}")
    app.run(host='0.0.0.0', port=5001, debug=False)