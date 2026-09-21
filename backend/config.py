"""
Configuration commune et initialisation des services
"""
from openai import OpenAI
import os
import logging
import chromadb
from chromadb.utils import embedding_functions
from dotenv import load_dotenv
from deepgram import DeepgramClient
from assemblyai.prerecorded.v2 import Transcriber
import assemblyai as aai
from meetings import MeetingManager
# --- CONFIGURATION LOGGING ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
# --- CHARGER LES VARIABLES D'ENVIRONNEMENT ---
load_dotenv()
# --- CLÉS API ---
ASSEMBLYAI_API_KEY = os.getenv("ASSEMBLYAI_API_KEY")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
# --- INITIALISATION ASSEMBLYAI ---
transcriber = Transcriber(api_key=ASSEMBLYAI_API_KEY)
logger.info("✅ AssemblyAI initialized")

# --- INITIALISATION CHROMADB ---
chroma_client = chromadb.PersistentClient(path="./email_vector_db")
embedding_function = embedding_functions.SentenceTransformerEmbeddingFunction(
    model_name="all-MiniLM-L6-v2"
)
# Collection commune : emails + transcriptions + Google Drive
email_collection = chroma_client.get_or_create_collection(
    name="email_summaries",
    embedding_function=embedding_function
)
# Alias plus explicite pour les nouvelles données
knowledge_collection = email_collection

# --- FICHIERS DE SUIVI ---
processed_emails_file = "processed_emails.json"
processed_drive_files_file = "processed_drive_files.json"

# --- EXTENSIONS ET MIME TYPES ---
TEXT_EXTENSIONS = {
    ".txt", ".md", ".csv", ".json", ".xml", ".html", ".htm", ".log"
}

AUDIO_EXTENSIONS = {
    ".mp3", ".m4a", ".wav", ".flac", ".ogg", ".aac", ".webm"
}

GOOGLE_DOC_MIME = "application/vnd.google-apps.document"
GOOGLE_SHEET_MIME = "application/vnd.google-apps.spreadsheet"
GOOGLE_SLIDE_MIME = "application/vnd.google-apps.presentation"

# --- SCOPES GOOGLE ---
GMAIL_SCOPES = ['https://www.googleapis.com/auth/gmail.readonly', 'https://www.googleapis.com/auth/gmail.send']#https://www.googleapis.com/auth/gmail.readonly
DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]
CALENDAR_SCOPES= ["https://www.googleapis.com/auth/calendar"]

# --- TOKEN FILES ---
GMAIL_TOKEN_FILE = "token.pickle"
DRIVE_TOKEN_FILE = "drive_token.pickle"
CALENDAR_TOKEN_FILE = "calendar_token.pickle"
GMAIL_SEND_TOKEN_FILE="token.pickle"

client = OpenAI(
            api_key=DEEPSEEK_API_KEY,
            base_url="https://api.deepseek.com"
        )
model="deepseek-v4-flash"


meetings_collection = chroma_client.get_or_create_collection(name="meetings_db")
meeting_manager = MeetingManager(chroma_db_meetings=meetings_collection)