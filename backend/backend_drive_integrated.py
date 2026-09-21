import pickle
from assemblyai.prerecorded.v2 import Transcriber
import tempfile
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
#from assemblyai.prerecorded.v2 import Transcriber
import assemblyai as aai
from deepgram import DeepgramClient
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from langchain_core.tools import tool
from langchain_core.messages import HumanMessage, AIMessage
from langgraph.prebuilt import create_react_agent
import logging
import uvicorn
import os
import time
import schedule
import json
import threading
from datetime import datetime, timedelta

import hashlib

from langchain_openai import ChatOpenAI
from dotenv import load_dotenv
from openai import OpenAI

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
import base64
import email
from email.mime.text import MIMEText
import io
import shutil
from pathlib import Path
from pypdf import PdfReader
from docx import Document
from openpyxl import load_workbook
from googleapiclient.http import MediaIoBaseDownload

# Nouveaux imports pour la vectorisation
import chromadb
from chromadb.utils import embedding_functions
from sentence_transformers import SentenceTransformer

# Configure logging for debugging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
#
app = FastAPI()

# Enable CORS for cross-platform access
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- CONFIGURATION ---

# --- INITIALISATION DEEPGRAM POUR TRANSCRIPTION ---
DEEPGRAM_API_KEY = "863c71021fa0421e89b5ab36b15e90a1ed8c4d47"
transcriber = Transcriber(api_key="eaead13c9a0e4924b898441053948f57")
aai.settings.api_key = "eaead13c9a0e4924b898441053948f57"
DEEPSEEK_API_KEY="sk-f73cca5a0970426abea7ef2fd3c3e7ca"
deepgram_client = None
if DEEPGRAM_API_KEY:
    deepgram_client = DeepgramClient(api_key=DEEPGRAM_API_KEY)
    logger.info("✅ Deepgram client initialized successfully")
else:
    logger.warning("⚠️ DEEPGRAM_API_KEY not set. Audio transcription will not be available.")

# --- INITIALISATION DES BASES DE DONNÉES ---
# Collection ChromaDB commune : emails + transcriptions + Google Drive
chroma_client = chromadb.PersistentClient(path="./email_vector_db")

embedding_function = embedding_functions.SentenceTransformerEmbeddingFunction(
    model_name="all-MiniLM-L6-v2"
)

# On garde le nom existant "email_summaries" pour ne pas perdre
# les données déjà indexées dans ton MVP.
email_collection = chroma_client.get_or_create_collection(
    name="email_summaries",
    embedding_function=embedding_function
)

# Alias plus explicite pour les nouvelles données.
knowledge_collection = email_collection

# Base pour suivre les emails déjà traités (pour éviter les doublons)
processed_emails_file = "processed_emails.json"

# 🔥 NOUVEAU : Base pour suivre les fichiers Drive déjà traités
processed_drive_files_file = "processed_drive_files.json"


def load_processed_emails() -> set:
    """Charge la liste des IDs d'emails déjà traités"""
    try:
        with open(processed_emails_file, 'r') as f:
            return set(json.load(f))
    except FileNotFoundError:
        # Créer le fichier avec une liste vide s'il n'existe pas
        with open(processed_emails_file, 'w') as f:
            json.dump([], f)
        return set()
    except json.JSONDecodeError:
        # Si le fichier est corrompu, on le réinitialise
        with open(processed_emails_file, 'w') as f:
            json.dump([], f)
        return set()


def save_processed_emails(processed_ids: set):
    """Sauvegarde la liste des IDs d'emails traités"""
    import json
    with open(processed_emails_file, 'w') as f:
        json.dump(list(processed_ids), f)


# 🔥 NOUVEAU : Fonction pour charger les fichiers Drive déjà traités
def load_processed_drive_files() -> dict:
    """Charge le suivi des fichiers Drive traités avec timestamp"""
    try:
        with open(processed_drive_files_file, 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        with open(processed_drive_files_file, 'w') as f:
            json.dump({}, f)
        return {}
    except json.JSONDecodeError:
        with open(processed_drive_files_file, 'w') as f:
            json.dump({}, f)
        return {}


# 🔥 NOUVEAU : Fonction pour sauvegarder les fichiers Drive traités
def save_processed_drive_files(processed_dict: dict):
    """Sauvegarde le suivi des fichiers Drive traités"""
    with open(processed_drive_files_file, 'w') as f:
        json.dump(processed_dict, f)


processed_emails = load_processed_emails()
processed_drive_files = load_processed_drive_files()  # 🔥 NOUVEAU


# --- TOOLS EXISTANTS ---
# --- RÉSUMÉ D'EMAIL ---
@tool
def summarize_email(email_content: str, sender: str = "", subject: str = "") -> str:
    """
    Résume le contenu d'un email.

    Args:
        email_content: Le contenu texte de l'email
        sender: L'expéditeur de l'email (optionnel)
        subject: Le sujet de l'email (optionnel)

    Returns:
        Un résumé concis de l'email
    """
    logger.info(f"Summarize tool called for email from {sender}")

    # Nettoyer le contenu (si c'est un contenu d'email complet)
    # On garde les 5000 premiers caractères pour respecter la limite
    content_preview = email_content[:5000]

    # Modèle de prompt pour le résumé
    prompt = f"""Tu es un assistant qui résume des emails de manière concise et professionnelle.

    {f"EXPÉDITEUR: {sender}" if sender else ""}
    {f"SUJET: {subject}" if subject else ""}

    CONTENU DE L'EMAIL:
    {content_preview}

    RÉSUMÉ (3-5 phrases claires et structurées):
    """

    try:
        # Utiliser Groq pour générer le résumé
        client = OpenAI(
            api_key=os.getenv("DEEPSEEK_API_KEY"),
            base_url="https://api.deepseek.com"
        )

        response = client.invoke(prompt)
        return response.content
    except Exception as e:
        logger.error(f"Error summarizing email: {str(e)}")
        return f"Erreur lors du résumé: {str(e)}"


# 🔥 NOUVEAU : Fonction pour résumer un document Drive
@tool
def summarize_drive_document(document_content: str, filename: str = "", file_type: str = "") -> str:
    """
    Résume le contenu d'un document Drive.

    Args:
        document_content: Le contenu textuel du document
        filename: Nom du fichier (optionnel)
        file_type: Type de fichier (optionnel)

    Returns:
        Un résumé concis du document
    """
    logger.info(f"Summarize tool called for Drive document: {filename}")

    # Limiter à 5000 caractères
    content_preview = document_content[:5000]

    prompt = f"""Tu es un assistant qui résume des documents de manière concise et structurée.

    {f"FICHIER: {filename}" if filename else ""}
    {f"TYPE: {file_type}" if file_type else ""}

    CONTENU:
    {content_preview}

    RÉSUMÉ (3-5 phrases claires et structurées):
    """

    try:
        client = OpenAI(
            api_key=os.getenv("DEEPSEEK_API_KEY"),
            base_url="https://api.deepseek.com"
        )

        response = client.invoke(prompt)
        return response.content
    except Exception as e:
        logger.error(f"Error summarizing Drive document: {str(e)}")
        return f"Erreur lors du résumé: {str(e)}"


@tool
def get_email_from_gmail(max_emails: int = 10) -> List[Dict[str, Any]]:
    """
    Récupère les derniers emails de la boîte Gmail (à configurer avec OAuth2).

    Args:
        max_emails: Nombre maximum d'emails à récupérer

    Returns:
        Liste d'emails avec leurs métadonnées
    """
    logger.info(f"Fetching last {max_emails} emails from Gmail")

    # NOTE: Vous devez configurer l'authentification OAuth2
    # Voici un exemple simplifié - à adapter avec vos credentials

    try:
        # Pour utiliser cette fonction, vous devez configurer:
        # 1. Un projet Google Cloud Console
        # 2. Activer l'API Gmail
        # 3. Créer des credentials OAuth2
        # 4. Télécharger le fichier credentials.json

        # Exemple de code (à décommenter et configurer)

        SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']
        creds = None

        # Charger les credentials existants
        if os.path.exists('token.pickle'):
            with open('token.pickle', 'rb') as token:
                creds = pickle.load(token)

        # Sinon, crée un nouveau
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                print("if")
                creds.refresh(Request())
            else:
                print("else")
                flow = InstalledAppFlow.from_client_secrets_file(
                    'credentials.json',  # Télécharge ça depuis Google Cloud Console
                    SCOPES
                )
                print("here0")
                creds = flow.run_local_server(port=0)

            with open('token.pickle', 'wb') as token:
                print("here1")
                pickle.dump(creds, token)

        service = build('gmail', 'v1', credentials=creds)

        # Récupérer les messages
        result = service.users().messages().list(
            userId='me',
            maxResults=max_emails,
            q='is:unread OR newer_than:1d'  # Filtrer les emails récents
        ).execute()

        messages = result.get('messages', [])
        emails = []

        for msg in messages:
            msg_data = service.users().messages().get(
                userId='me',
                id=msg['id']
            ).execute()

            payload = msg_data.get('payload', {})
            headers = payload.get('headers', [])

            # Extraire les headers
            subject = next((h['value'] for h in headers if h['name'] == 'Subject'), 'Sans sujet')
            sender = next((h['value'] for h in headers if h['name'] == 'From'), 'Inconnu')
            date = next((h['value'] for h in headers if h['name'] == 'Date'), '')

            # Extraire le contenu
            body = ''
            if 'parts' in payload:
                for part in payload['parts']:
                    if part['mimeType'] == 'text/plain':
                        data = part['body'].get('data', '')
                        if data:
                            body = base64.urlsafe_b64decode(data).decode('utf-8')
                            break
            elif 'body' in payload and 'data' in payload['body']:
                body = base64.urlsafe_b64decode(payload['body']['data']).decode('utf-8')

            emails.append({
                'id': msg['id'],
                'subject': subject,
                'from': sender,
                'date': date,
                'body': body
            })

        return emails

    except FileNotFoundError:
        logger.warning("credentials.json not found. Please set up Google OAuth2 authentication.")
        return [{"error": "Gmail OAuth2 not configured. Please set up credentials.json"}]
    except Exception as e:
        logger.error(f"Error fetching emails from Gmail: {str(e)}")
        return [{"error": str(e)}]


# --- TOOLS: RECHERCHE ET TRAITEMENT D'EMAILS ---
@tool
def search_emails(query: str, limit: int = 5) -> str:
    """
    Recherche dans les résumés d'emails vectorisés stockés dans ChromaDB.

    Args:
        query: La requête de recherche
        limit: Nombre de résultats à retourner

    Returns:
        Les résultats de la recherche formatés
    """
    logger.info(f"Searching emails with query: {query}")

    try:
        results = email_collection.query(
            query_texts=[query],
            n_results=limit
        )

        if not results['documents'] or not results['documents'][0]:
            return "Aucun email correspondant à votre recherche."

        formatted_results = []
        for i, doc in enumerate(results['documents'][0], 1):
            metadata = results['metadatas'][0][i - 1] if results['metadatas'] else {}
            formatted_results.append(
                f"{i}. **{metadata.get('subject', 'Sans sujet')}** "
                f"(de {metadata.get('sender', 'Inconnu')})\n"
                f"   {doc[:200]}..."
            )

        return "\n\n".join(formatted_results)

    except Exception as e:
        logger.error(f"Error searching emails: {str(e)}")
        return f"Erreur lors de la recherche: {str(e)}"


@tool
def process_and_summarize_emails(placeholder: str = "") -> str:
    """
    Récupère les nouveaux emails de Gmail, les résume avec Groq,
    et les stocke dans la base vectorielle ChromaDB.

    Args:
        placeholder: Paramètre vide pour compatibilité avec l'agent

    Returns:
        Un message indiquant le nombre d'emails traités
    """
    logger.info("Starting email processing and summarization...")

    try:
        # Récupérer les emails
        emails = get_email_from_gmail.invoke({"max_emails": 10})

        if isinstance(emails, list) and len(emails) > 0 and "error" in emails[0]:
            return f"Impossible de récupérer les emails: {emails[0]['error']}"

        if not emails:
            return "Aucun email trouvé."

        summaries = []
        processed_count = 0

        for email_data in emails:
            email_id = email_data.get('id', '')

            # Vérifier si l'email a déjà été traité
            if email_id in processed_emails:
                logger.info(f"Email {email_id} already processed, skipping...")
                continue

            # Résumer l'email avec Groq
            summary = summarize_email.invoke({
                "email_content": email_data.get('body', ''),
                "sender": email_data.get('from', ''),
                "subject": email_data.get('subject', '')
            })

            # Préparer les métadonnées
            metadata = {
                "type": "email",
                "source": "gmail",
                "sender": email_data.get('from', ''),
                "subject": email_data.get('subject', ''),
                "date": email_data.get('date', ''),
                "summary": summary
            }

            email_collection.add(
                documents=[email_data.get('body', '')],
                metadatas=[metadata],
                ids=[email_id]
            )

            processed_emails.add(email_id)
            processed_count += 1
            summaries.append(f"• {metadata['subject']} (de {metadata['sender']})")

            logger.info(f"Processed email: {email_id}")

        # Sauvegarder la liste des emails traités
        save_processed_emails(processed_emails)

        if processed_count == 0:
            return "Aucun nouvel email à traiter."

        return f"✅ {processed_count} emails traités et résumés:\n\n" + "\n".join(summaries)

    except Exception as e:
        logger.error(f"Error in email processing: {str(e)}")
        return f"Erreur lors du traitement: {str(e)}"


# ============================================================
# GOOGLE DRIVE -> CHROMADB
# ============================================================

DRIVE_SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly"
]

DRIVE_TOKEN_FILE = "drive_token.pickle"

TEXT_EXTENSIONS = {
    ".txt", ".md", ".csv", ".json", ".xml", ".html", ".htm", ".log"
}

AUDIO_EXTENSIONS = {
    ".mp3", ".m4a", ".wav", ".flac", ".ogg", ".aac", ".webm"
}

GOOGLE_DOC_MIME = "application/vnd.google-apps.document"
GOOGLE_SHEET_MIME = "application/vnd.google-apps.spreadsheet"
GOOGLE_SLIDE_MIME = "application/vnd.google-apps.presentation"


def get_drive_service():
    """Authentifie l'utilisateur et retourne le service Google Drive."""

    creds = None

    if os.path.exists(DRIVE_TOKEN_FILE):
        with open(DRIVE_TOKEN_FILE, "rb") as token:
            creds = pickle.load(token)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists("drive_credentials.json"):
                raise FileNotFoundError(
                    "drive_credentials.json introuvable. "
                    "Place-le à côté de ton backend."
                )

            flow = InstalledAppFlow.from_client_secrets_file(
                "drive_credentials.json",
                DRIVE_SCOPES
            )
            creds = flow.run_local_server(port=0)

        with open(DRIVE_TOKEN_FILE, "wb") as token:
            pickle.dump(creds, token)

    return build("drive", "v3", credentials=creds)


def _list_drive_files(service, page_size: int = 100):
    """Fonction interne: liste tous les fichiers non supprimés de Google Drive."""
    files = []
    page_token = None

    while True:
        response = service.files().list(
            pageSize=page_size,
            pageToken=page_token,
            q="trashed = false",
            orderBy="modifiedTime desc",
            fields=(
                "nextPageToken,"
                "files("
                "id,name,mimeType,size,modifiedTime,webViewLink,parents"
                ")"
            )
        ).execute()

        files.extend(response.get("files", []))
        page_token = response.get("nextPageToken")

        if not page_token:
            break

    return files


@tool
def list_drive_files(page_size: int = 100) -> str:
    """
    Liste les fichiers accessibles dans Google Drive.

    Cet outil est directement utilisable par l'agent, comme les outils Gmail.
    Il gère lui-même l'authentification OAuth2 et ne demande jamais un objet
    Google Drive 'service' en argument.

    Args:
        page_size: Nombre maximum de fichiers récupérés par page.

    Returns:
        Liste formatée des fichiers Drive avec leurs métadonnées.
    """
    logger.info(f"📁 Tool list_drive_files called (page_size={page_size})")

    try:
        service = get_drive_service()
        files = _list_drive_files(service, page_size=page_size)

        if not files:
            return "Google Drive est accessible, mais aucun fichier non supprimé n'a été trouvé."

        formatted = []
        for index, drive_file in enumerate(files, 1):
            name = drive_file.get("name", "Sans nom")
            file_id = drive_file.get("id", "")
            mime_type = drive_file.get("mimeType", "")
            modified_time = drive_file.get("modifiedTime", "")
            link = drive_file.get("webViewLink", "")

            formatted.append(
                f"{index}. **{name}**\n"
                f"   ID: {file_id}\n"
                f"   Type: {mime_type}\n"
                f"   Modifié: {modified_time}\n"
                f"   Lien: {link or 'non disponible'}"
            )

        return f"📁 {len(files)} fichier(s) trouvé(s) dans Google Drive :\n\n" + "\n\n".join(formatted)

    except FileNotFoundError as e:
        logger.error(f"❌ Drive OAuth configuration missing: {e}")
        return f"Impossible d'accéder à Google Drive : {e}"
    except Exception as e:
        logger.error(f"❌ Error listing Google Drive files: {e}", exc_info=True)
        return f"Erreur lors de la récupération des fichiers Drive : {str(e)}"


def download_drive_file(service, file_id: str, destination: str):
    """Télécharge un fichier binaire depuis Google Drive."""

    request = service.files().get(
        fileId=file_id,
        alt="media"
    )

    with open(destination, "wb") as fh:
        downloader = MediaIoBaseDownload(fh, request)
        done = False

        while not done:
            status, done = downloader.next_chunk()
            if status:
                logger.info(
                    f"Download {int(status.progress() * 100)}%"
                )

    return destination


def export_google_file(
        service,
        file_id: str,
        mime_type: str,
        destination: str
):
    """Exporte un Google Doc/Sheet/Slide vers un format local."""

    export_map = {
        GOOGLE_DOC_MIME:
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        GOOGLE_SHEET_MIME:
            "text/csv",
        GOOGLE_SLIDE_MIME:
            "text/plain"
    }

    export_mime = export_map.get(mime_type)

    if not export_mime:
        raise ValueError(
            f"Type Google Workspace non supporté: {mime_type}"
        )

    request = service.files().export_media(
        fileId=file_id,
        mimeType=export_mime
    )

    with open(destination, "wb") as fh:
        downloader = MediaIoBaseDownload(fh, request)
        done = False

        while not done:
            status, done = downloader.next_chunk()
            if status:
                logger.info(
                    f"Export {int(status.progress() * 100)}%"
                )

    return destination


def get_file_category(filename: str, mime_type: str):
    """Détermine la catégorie d'un fichier Drive."""

    extension = Path(filename).suffix.lower()

    if mime_type == GOOGLE_DOC_MIME:
        return "google_doc"

    if mime_type == GOOGLE_SHEET_MIME:
        return "google_sheet"

    if mime_type == GOOGLE_SLIDE_MIME:
        return "google_slide"

    if extension in AUDIO_EXTENSIONS or mime_type.startswith("audio/"):
        return "audio"

    if extension == ".pdf" or mime_type == "application/pdf":
        return "pdf"

    if extension == ".docx":
        return "docx"

    if extension == ".xlsx":
        return "xlsx"

    if extension in TEXT_EXTENSIONS or mime_type.startswith("text/"):
        return "text"

    return "unknown"


def extract_pdf_text(file_path: str) -> str:
    reader = PdfReader(file_path)
    pages = []

    for page in reader.pages:
        text = page.extract_text()
        if text:
            pages.append(text)

    return "\n\n".join(pages)


def extract_docx_text(file_path: str) -> str:
    document = Document(file_path)
    parts = []

    for paragraph in document.paragraphs:
        text = paragraph.text.strip()
        if text:
            parts.append(text)

    # Inclure également le contenu des tableaux Word.
    for table in document.tables:
        for row in table.rows:
            cells = [
                cell.text.strip()
                for cell in row.cells
                if cell.text.strip()
            ]
            if cells:
                parts.append(" | ".join(cells))

    return "\n".join(parts)


def extract_xlsx_text(file_path: str) -> str:
    workbook = load_workbook(
        filename=file_path,
        read_only=True,
        data_only=True
    )

    parts = []

    for sheet in workbook.worksheets:
        parts.append(f"FEUILLE: {sheet.title}")

        for row in sheet.iter_rows(values_only=True):
            values = [
                str(value).strip()
                for value in row
                if value is not None and str(value).strip()
            ]

            if values:
                parts.append(" | ".join(values))

    workbook.close()
    return "\n".join(parts)


def extract_text_file(file_path: str) -> str:
    """Lit un fichier texte avec plusieurs encodages de secours."""

    for encoding in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            with open(
                    file_path,
                    "r",
                    encoding=encoding
            ) as f:
                return f.read()
        except UnicodeDecodeError:
            continue

    raise ValueError(
        f"Impossible de lire le fichier texte: {file_path}"
    )


def transcribe_drive_audio(file_path: str) -> str:
    """Transcrit un fichier audio Drive avec AssemblyAI."""

    logger.info(f"🎙️ Transcription Drive: {file_path}")

    transcriber = aai.Transcriber()
    transcript = transcriber.transcribe(file_path)

    if transcript.status == aai.TranscriptStatus.error:
        raise RuntimeError(
            f"AssemblyAI error: {transcript.error}"
        )

    if not transcript.text or not transcript.text.strip():
        raise RuntimeError(
            "AssemblyAI n'a retourné aucun texte."
        )

    return transcript.text.strip()


def extract_drive_content(
        file_path: str,
        filename: str,
        mime_type: str
):
    """
    Extrait le contenu textuel d'un fichier Drive.
    Pour l'audio, retourne la transcription AssemblyAI.
    """

    category = get_file_category(
        filename,
        mime_type
    )

    if category == "pdf":
        return extract_pdf_text(file_path), category

    if category == "docx":
        return extract_docx_text(file_path), category

    if category == "xlsx":
        return extract_xlsx_text(file_path), category

    if category == "text":
        return extract_text_file(file_path), category

    if category == "audio":
        return transcribe_drive_audio(file_path), category

    # Les Google Workspace files sont exportés avant cette fonction.
    if category == "google_doc":
        return extract_docx_text(file_path), category

    if category in {"google_sheet", "google_slide"}:
        return extract_text_file(file_path), category

    raise ValueError(
        f"Type de fichier non supporté: {filename} ({mime_type})"
    )


def split_text(
        text: str,
        chunk_size: int = 1200,
        chunk_overlap: int = 200
):
    """Découpe un texte en morceaux adaptés à la recherche vectorielle."""

    text = (text or "").strip()

    if not text:
        return []

    if chunk_overlap >= chunk_size:
        raise ValueError(
            "chunk_overlap doit être inférieur à chunk_size."
        )

    chunks = []
    start = 0
    text_length = len(text)

    while start < text_length:
        end = min(start + chunk_size, text_length)
        chunk = text[start:end].strip()

        if chunk:
            chunks.append(chunk)

        if end >= text_length:
            break

        start = end - chunk_overlap

    return chunks


def remove_existing_drive_chunks(file_id: str):
    """Supprime les anciens chunks d'un fichier avant réindexation."""

    try:
        existing = knowledge_collection.get(
            where={"drive_file_id": file_id}
        )

        existing_ids = existing.get("ids", [])

        if existing_ids:
            knowledge_collection.delete(ids=existing_ids)
            logger.info(
                f"🗑️ {len(existing_ids)} anciens chunks supprimés "
                f"pour Drive file {file_id}"
            )

    except Exception as e:
        # Une collection Chroma peut contenir des documents sans ce champ
        # (anciens emails). Ce cas ne doit pas empêcher la synchronisation.
        logger.warning(
            f"Impossible de supprimer les anciens chunks "
            f"du fichier {file_id}: {e}"
        )


def save_drive_content_to_chroma(
        file_id: str,
        filename: str,
        mime_type: str,
        category: str,
        content: str,
        modified_time: str = "",
        web_view_link: str = ""
):
    """Découpe et sauvegarde le contenu Drive dans ChromaDB."""

    content = (content or "").strip()

    if not content:
        logger.warning(
            f"⚠️ Aucun contenu textuel: {filename}"
        )
        return 0

    chunks = split_text(content)

    if not chunks:
        return 0

    # Permet de réindexer un fichier modifié sans doublons.
    remove_existing_drive_chunks(file_id)

    documents = []
    metadatas = []
    ids = []

    for index, chunk in enumerate(chunks):
        documents.append(chunk)

        metadatas.append({
            "type": "drive_document",
            "source": "google_drive",
            "drive_file_id": file_id,
            "filename": filename,
            "mime_type": mime_type,
            "category": category,
            "chunk_index": index,
            "modified_time": modified_time or "",
            "web_view_link": web_view_link or ""
        })

        ids.append(
            f"drive_{hashlib.sha256(file_id.encode()).hexdigest()[:16]}_{index}"
        )

    knowledge_collection.add(
        documents=documents,
        metadatas=metadatas,
        ids=ids
    )

    logger.info(
        f"💾 {filename}: {len(chunks)} chunks enregistrés dans ChromaDB"
    )

    return len(chunks)


def sync_google_drive():
    """
    Synchronise les fichiers supportés de Google Drive vers ChromaDB.

    ✨ AMÉLIORATIONS:
    - Suivi des fichiers déjà traités (comme les emails)
    - Résumé des documents texte
    - Métadonnées cohérentes avec les emails

    Documents texte -> extraction
    Audio -> transcription AssemblyAI
    Puis chunking + embeddings + ChromaDB.
    """

    logger.info("🚀 Starting Google Drive synchronization...")

    service = get_drive_service()
    files = _list_drive_files(service)

    logger.info(
        f"📁 {len(files)} fichiers trouvés dans Google Drive"
    )

    indexed = 0
    skipped = 0
    failed = 0
    already_processed = 0  # 🔥 NOUVEAU
    results = []

    temp_dir = tempfile.mkdtemp(
        prefix="drive_sync_"
    )

    try:
        for drive_file in files:

            file_id = drive_file.get("id", "")
            filename = drive_file.get("name", "")
            mime_type = drive_file.get("mimeType", "")
            modified_time = drive_file.get(
                "modifiedTime", ""
            )
            web_view_link = drive_file.get(
                "webViewLink", ""
            )

            logger.info(
                f"➡️ Drive: {filename} | {mime_type}"
            )

            # 🔥 NOUVEAU : Vérifier si le fichier a déjà été traité
            if file_id in processed_drive_files:
                stored_time = processed_drive_files[file_id].get("modified_time", "")
                # Si le fichier n'a pas changé depuis, le sauter
                if stored_time == modified_time:
                    logger.info(f"📌 {filename} déjà traité et inchangé, skipping...")
                    already_processed += 1
                    results.append({
                        "filename": filename,
                        "file_id": file_id,
                        "status": "already_processed",
                        "reason": "file_unchanged"
                    })
                    continue
                else:
                    logger.info(f"🔄 {filename} a été modifié, retraitement...")
                    remove_existing_drive_chunks(file_id)

            try:
                category = get_file_category(
                    filename,
                    mime_type
                )

                if category == "unknown":
                    skipped += 1

                    results.append({
                        "filename": filename,
                        "file_id": file_id,
                        "status": "skipped",
                        "reason": "unsupported_type",
                        "mime_type": mime_type
                    })

                    continue

                is_google_workspace = mime_type.startswith(
                    "application/vnd.google-apps."
                )

                # Extension temporaire correcte pour l'extraction.
                if mime_type == GOOGLE_DOC_MIME:
                    local_extension = ".docx"
                elif mime_type == GOOGLE_SHEET_MIME:
                    local_extension = ".csv"
                elif mime_type == GOOGLE_SLIDE_MIME:
                    local_extension = ".txt"
                else:
                    local_extension = Path(filename).suffix.lower()

                if not local_extension:
                    local_extension = ".bin"

                local_path = os.path.join(
                    temp_dir,
                    f"{hashlib.sha256(file_id.encode()).hexdigest()}"
                    f"{local_extension}"
                )

                # ----------------------------
                # DOWNLOAD / EXPORT
                # ----------------------------
                if is_google_workspace:
                    export_google_file(
                        service=service,
                        file_id=file_id,
                        mime_type=mime_type,
                        destination=local_path
                    )
                else:
                    download_drive_file(
                        service=service,
                        file_id=file_id,
                        destination=local_path
                    )

                # ----------------------------
                # EXTRACTION / TRANSCRIPTION
                # ----------------------------
                content, extracted_category = extract_drive_content(
                    file_path=local_path,
                    filename=filename,
                    mime_type=mime_type
                )

                content = (content or "").strip()

                if not content:
                    skipped += 1

                    results.append({
                        "filename": filename,
                        "file_id": file_id,
                        "status": "skipped",
                        "reason": "empty_content",
                        "category": extracted_category
                    })

                    continue

                # 🔥 NOUVEAU : Résumer le document (optionnel)
                summary = ""
                try:
                    if extracted_category not in ["audio", "transcription"]:
                        summary = summarize_drive_document.invoke({
                            "document_content": content[:2000],
                            "filename": filename,
                            "file_type": extracted_category
                        })
                        logger.info(f"✅ Résumé généré pour {filename}")
                except Exception as e:
                    logger.warning(f"⚠️ Impossible de résumer {filename}: {e}")

                # ----------------------------
                # CHROMADB
                # ----------------------------
                chunks_count = save_drive_content_to_chroma(
                    file_id=file_id,
                    filename=filename,
                    mime_type=mime_type,
                    category=extracted_category,
                    content=content,
                    modified_time=modified_time,
                    web_view_link=web_view_link
                )

                indexed += 1

                # 🔥 NOUVEAU : Marquer le fichier comme traité
                processed_drive_files[file_id] = {
                    "filename": filename,
                    "modified_time": modified_time,
                    "processed_at": datetime.now().isoformat(),
                    "chunks_count": chunks_count
                }
                save_processed_drive_files(processed_drive_files)

                results.append({
                    "filename": filename,
                    "file_id": file_id,
                    "status": "indexed",
                    "category": extracted_category,
                    "chunks": chunks_count,
                    "characters": len(content),
                    "summary": summary  # 🔥 NOUVEAU
                })

            except Exception as e:
                failed += 1

                logger.error(
                    f"❌ Erreur avec {filename}: {e}",
                    exc_info=True
                )

                results.append({
                    "filename": filename,
                    "file_id": file_id,
                    "status": "error",
                    "error": str(e)
                })

    finally:
        shutil.rmtree(
            temp_dir,
            ignore_errors=True
        )

    logger.info(
        f"✅ Drive sync terminée | "
        f"indexed={indexed}, already_processed={already_processed}, "  # 🔥 NOUVEAU
        f"skipped={skipped}, failed={failed}"
    )

    return {
        "total_files": len(files),
        "indexed": indexed,
        "already_processed": already_processed,  # 🔥 NOUVEAU
        "skipped": skipped,
        "failed": failed,
        "chromadb_total_chunks": knowledge_collection.count(),
        "results": results
    }


@tool
def sync_drive_tool(placeholder: str = "") -> str:
    """
    Synchronise Google Drive vers ChromaDB.
    Outil directement utilisable par l'agent, sans objet service en argument.
    """
    logger.info("🚀 Agent tool: synchronisation Google Drive demandée")

    try:
        result = sync_google_drive()
        return (
            f"✅ Synchronisation Google Drive terminée. "
            f"Total: {result['total_files']}, "
            f"indexés: {result['indexed']}, "
            f"déjà traités: {result['already_processed']}, "
            f"ignorés: {result['skipped']}, "
            f"erreurs: {result['failed']}."
        )
    except Exception as e:
        logger.error(f"❌ Drive sync tool error: {e}", exc_info=True)
        return f"Erreur lors de la synchronisation Google Drive : {str(e)}"


class KnowledgeQuery(BaseModel):
    query: str
    limit: Optional[int] = 5


@tool
def search_knowledge(query: str, limit: int = 5) -> str:
    """
    Recherche dans Gmail, Google Drive et les transcriptions
    enregistrés dans ChromaDB.
    """

    try:
        results = knowledge_collection.query(
            query_texts=[query],
            n_results=max(1, min(limit, 20))
        )

        documents = results.get("documents", [[]])[0]
        metadatas = results.get("metadatas", [[]])[0]

        if not documents:
            return "Aucun contenu correspondant trouvé."

        formatted = []

        for i, doc in enumerate(documents, 1):
            metadata = (
                metadatas[i - 1]
                if i - 1 < len(metadatas)
                else {}
            )

            source_type = metadata.get(
                "type",
                "unknown"
            )

            filename = metadata.get(
                "filename",
                metadata.get("subject", "Sans titre")
            )

            sender = metadata.get(
                "sender",
                ""
            )

            source_label = (
                f"{filename}"
                if not sender
                else f"{filename} — {sender}"
            )

            formatted.append(
                f"{i}. [{source_type}] {source_label}\n"
                f"{doc[:1000]}"
            )

        return "\n\n".join(formatted)

    except Exception as e:
        logger.error(
            f"Error searching knowledge: {e}",
            exc_info=True
        )
        return f"Erreur lors de la recherche: {str(e)}"


# --- SCHEDULER POUR EXÉCUTION HORAIRE ---
def scheduled_email_processing():
    """Fonction exécutée par le scheduler toutes les heures"""
    logger.info("⏰ Running scheduled email processing...")
    result = process_and_summarize_emails.invoke({})
    logger.info(f"📊 Scheduled result: {result}")
    return result


# Démarrer le scheduler dans un thread séparé
def start_scheduler():
    """Démarre le scheduler dans un thread background"""
    schedule.every().hour.do(scheduled_email_processing)
    # Optionnel: exécuter immédiatement au démarrage
    # schedule.every(1).minutes.do(scheduled_email_processing)

    logger.info("🔄 Scheduler started - emails will be processed every hour")

    while True:
        schedule.run_pending()
        time.sleep(60)  # Vérifier chaque minute


# Lancer le scheduler dans un thread daemon
scheduler_thread = threading.Thread(target=start_scheduler, daemon=True)
scheduler_thread.start()

# --- INITIALISATION DE L'AGENT AVEC TOUS LES OUTILS ---
tools = [
    process_and_summarize_emails,
    search_emails,
    summarize_email,
    list_drive_files,
    sync_drive_tool,
    summarize_drive_document,
    search_knowledge
]
# Note: get_email_from_gmail est utilisé en interne par process_and_summarize_emails

# Initialize the LLM
llm = ChatOpenAI(
    model="deepseek-v4-flash",  # ou "deepseek-reasoner"
    temperature=0.7,
    api_key=DEEPSEEK_API_KEY,  # ta clé DeepSeek
    base_url="https://api.deepseek.com"  # ← indispensable
)

# Create agent
try:
    agent = create_react_agent(
        model=llm,
        tools=tools,
        prompt="""Tu es un assistant IA utile avec accès aux outils suivants:
        - process_and_summarize_emails: Traiter et résumer les nouveaux emails (argument: placeholder, laisse vide)
        - search_emails: Rechercher dans les emails (arguments: query, limit)
        - summarize_email: Résumer un email spécifique (arguments: email_content, sender, subject)
        - list_drive_files: Lister les fichiers accessibles dans Google Drive (argument: page_size)
        - sync_drive_tool: Synchroniser Google Drive vers ChromaDB (argument: placeholder, laisse vide)
        - summarize_drive_document: Résumer un document Drive (arguments: document_content, filename, file_type)
        - search_knowledge: Rechercher dans la base de connaissances ChromaDB, qui contient les emails, les documents Google Drive et les transcriptions audio (arguments: query, limit)

        RÈGLES IMPORTANTES:
        - Pour "liste mes fichiers Drive", "quels sont mes fichiers Drive", "montre-moi mon Drive" ou toute demande de consultation directe de Google Drive, utilise list_drive_files.
        - Pour rechercher le contenu déjà indexé des documents Drive, utilise search_knowledge.
        - Pour importer/indexer les nouveaux fichiers Drive dans ChromaDB, utilise sync_drive.
        - Pour les questions concernant un document Drive déjà indexé, une réunion transcrite ou une information générale susceptible d'être dans la base, utilise search_knowledge.
        - Pour utiliser process_and_summarize_emails, appelle-le avec placeholder="".
        """
    )
    logger.info("Agent created successfully with email tools and audio transcription")
except Exception as e:
    logger.error(f"Error creating agent: {str(e)}")
    raise


# --- REQUEST/RESPONSE MODELS ---
class ChatRequest(BaseModel):
    message: str
    history: Optional[List[dict]] = None


class ChatResponse(BaseModel):
    response: str
    tool_calls: Optional[List[dict]] = []


# --- ENDPOINTS EXISTANTS ---
@app.get("/")
async def root():
    """Health check endpoint"""
    return {"message": "AI Assistant API is running", "status": "ok"}


@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """
    Chat endpoint that processes user messages and returns AI responses
    """
    try:
        logger.info(f"Received message: {request.message}")

        # Build message history
        messages = []
        if request.history:
            for msg in request.history:
                if msg.get("role") == "user":
                    messages.append(HumanMessage(content=msg.get("content", "")))
                elif msg.get("role") == "assistant":
                    messages.append(AIMessage(content=msg.get("content", "")))

        messages.append(HumanMessage(content=request.message))

        logger.info(f"Invoking agent with {len(messages)} messages")

        result = agent.invoke({"messages": messages})

        logger.info(f"Agent result: {result}")

        response_messages = result.get("messages", [])
        final_response = ""
        tool_calls = []

        if response_messages:
            last_msg = response_messages[-1]
            if hasattr(last_msg, 'content'):
                final_response = last_msg.content
            else:
                final_response = str(last_msg)

            for msg in response_messages:
                if hasattr(msg, 'tool_calls') and msg.tool_calls:
                    for call in msg.tool_calls:
                        if isinstance(call, dict):
                            tool_calls.append({
                                "name": call.get('name', call.get('tool', 'unknown')),
                                "args": call.get('args', call.get('input', {}))
                            })
                        else:
                            tool_calls.append({
                                "name": getattr(call, 'name', getattr(call, 'tool', 'unknown')),
                                "args": getattr(call, 'args', getattr(call, 'input', {}))
                            })

        logger.info(f"Response: {final_response}")

        return ChatResponse(
            response=final_response,
            tool_calls=tool_calls
        )

    except HTTPException as http_err:
        logger.error(f"HTTP Error: {http_err.detail}")
        raise http_err
    except Exception as e:
        logger.error(f"Error processing message: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={
                "error": str(e),
                "type": type(e).__name__,
                "message": "An error occurred while processing your request."
            }
        )


@app.get("/health")
async def health_check():
    """Detailed health check endpoint"""
    return {
        "status": "healthy",
        "llm_model": "openai/gpt-oss-120b",
        "tools_available": [tool.name for tool in tools],
        "agent_ready": True,
        "processed_emails": len(processed_emails),
        "stored_items": knowledge_collection.count(),
        "stored_emails": email_collection.count(),
        "deepgram_available": deepgram_client is not None,
        "mails_available": os.path.exists("credentials.json"),
        "drive_available": os.path.exists("drive_credentials.json")
    }


# --- ENDPOINT POUR TRANSCRIPTION AUDIO ---
@app.post("/transcribe")
async def transcribe(file: UploadFile = File(...)):
    """
    Endpoint pour transcrire un fichier audio.

    Formats supportés notamment :
    .m4a, .mp3, .wav, .flac, .ogg, etc.
    """

    try:
        logger.info(f"📁 Transcription request: {file.filename}")
        logger.info(f"📊 Content type: {file.content_type}")

        # Vérification basique
        if not file.filename:
            raise HTTPException(
                status_code=400,
                detail="Aucun fichier fourni."
            )

        # Lire le fichier uploadé
        audio_data = await file.read()

        if not audio_data:
            raise HTTPException(
                status_code=400,
                detail="Le fichier audio est vide."
            )

        logger.info(
            f"📦 Audio reçu: {len(audio_data) / 1024:.2f} KB"
        )

        # Créer un fichier temporaire
        suffix = os.path.splitext(file.filename)[1] or ".m4a"

        with tempfile.NamedTemporaryFile(
                delete=False,
                suffix=suffix
        ) as temp_file:

            temp_file.write(audio_data)
            temp_path = temp_file.name

        try:
            logger.info(f"🎙️ Transcription de: {temp_path}")

            # ==========================================
            # ASSEMBLYAI
            # ==========================================

            transcriber = aai.Transcriber()

            transcript = transcriber.transcribe(temp_path)

            if transcript.status == aai.TranscriptStatus.error:
                raise RuntimeError(
                    f"AssemblyAI error: {transcript.error}"
                )

            text = transcript.text or ""

            if not text.strip():
                raise RuntimeError(
                    "AssemblyAI n'a retourné aucun texte."
                )

            logger.info("✅ Transcription successful")

            # ==========================================
            # SAUVEGARDE DANS CHROMADB
            # ==========================================

            if text and text.strip():

                # ID unique pour la transcription
                transcription_id = (
                        "transcription_" +
                        hashlib.sha256(
                            f"{file.filename}_{datetime.now().isoformat()}".encode()
                        ).hexdigest()
                )

                # Métadonnées
                metadata = {
                    "type": "transcription",
                    "filename": file.filename,
                    "date": datetime.now().isoformat(),
                    "source": "AssemblyAI"
                }

                # Ajouter la transcription dans ChromaDB
                email_collection.add(
                    documents=[text],
                    metadatas=[metadata],
                    ids=[transcription_id]
                )

                logger.info(
                    f"💾 Transcription saved in ChromaDB: {transcription_id}"
                )

            else:
                logger.warning("⚠️ Empty transcription, nothing saved")

            return {
                "filename": file.filename,
                "text": text,
                "status": "success",
                "mode": "AssemblyAI",
                "saved_to_chromadb": True
            }

        finally:
            # Supprimer le fichier temporaire
            if os.path.exists(temp_path):
                os.remove(temp_path)
                logger.info("🗑️ Temporary file deleted")

    except HTTPException:
        raise

    except Exception as e:
        logger.error(
            f"❌ Error during transcription: {str(e)}",
            exc_info=True
        )

        raise HTTPException(
            status_code=500,
            detail=f"Erreur lors de la transcription : {str(e)}"
        )


# ============================================================
# ENDPOINTS GOOGLE DRIVE / KNOWLEDGE
# ============================================================

@app.get("/drive/files")
async def get_drive_files():
    """Retourne la liste des fichiers présents dans Google Drive."""

    try:
        service = get_drive_service()
        files = _list_drive_files(service)

        return {
            "count": len(files),
            "files": files
        }

    except Exception as e:
        logger.error(
            f"Drive listing error: {e}",
            exc_info=True
        )
        raise HTTPException(
            status_code=500,
            detail=str(e)
        )


@app.post("/drive/sync")
async def sync_drive():
    """
    Synchronise Google Drive -> extraction/transcription -> ChromaDB.
    """

    try:
        result = sync_google_drive()

        return {
            "status": "success",
            **result
        }

    except Exception as e:
        logger.error(
            f"Drive sync error: {e}",
            exc_info=True
        )
        raise HTTPException(
            status_code=500,
            detail=str(e)
        )


@app.post("/knowledge/search")
async def search_knowledge_endpoint(query: KnowledgeQuery):
    """Recherche sémantique dans toute la base ChromaDB."""

    try:
        results = knowledge_collection.query(
            query_texts=[query.query],
            n_results=max(1, min(query.limit or 5, 20))
        )

        documents = results.get("documents", [[]])[0]
        metadatas = results.get("metadatas", [[]])[0]
        ids = results.get("ids", [[]])[0]
        distances = results.get("distances", [[]])[0]

        output = []

        for i, document in enumerate(documents):
            output.append({
                "id": ids[i] if i < len(ids) else None,
                "content": document,
                "metadata": (
                    metadatas[i]
                    if i < len(metadatas)
                    else {}
                ),
                "distance": (
                    distances[i]
                    if i < len(distances)
                    else None
                )
            })

        return {
            "query": query.query,
            "count": len(output),
            "results": output
        }

    except Exception as e:
        logger.error(
            f"Knowledge search error: {e}",
            exc_info=True
        )
        raise HTTPException(
            status_code=500,
            detail=str(e)
        )


@app.get("/knowledge/stats")
async def knowledge_stats():
    """Statistiques de la base de connaissances."""

    try:
        total = knowledge_collection.count()

        return {
            "collection": "email_summaries",
            "total_chunks": total,
            "emails_processed": len(processed_emails),
            "drive_files_processed": len(processed_drive_files)  # 🔥 NOUVEAU
        }

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=str(e)
        )


# 🔥 NOUVEAU : Endpoint pour obtenir les stats complètes (emails + drive)
@app.get("/sync/status")
async def get_sync_status():
    """Statistiques complètes de synchronisation"""
    return {
        "emails": {
            "total_processed": len(processed_emails),
            "stored": email_collection.count()
        },
        "drive_files": {
            "total_processed": len(processed_drive_files),
            "files": processed_drive_files
        },
        "chromadb_total": knowledge_collection.count(),
        "last_updated": datetime.now().isoformat()
    }


# --- NOUVEAUX ENDPOINTS POUR LES EMAILS ---
class EmailQuery(BaseModel):
    query: str
    limit: Optional[int] = 5


@app.post("/emails/search")
async def search_email_summaries(query: EmailQuery):
    """Endpoint pour rechercher dans les résumés d'emails"""
    try:
        results = search_emails.invoke({
            "query": query.query,
            "limit": query.limit
        })
        return {"result": results}
    except Exception as e:
        logger.error(f"Error in search: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/emails/process")
async def process_emails_now():
    """Endpoint pour déclencher manuellement le traitement des emails"""
    try:
        result = process_and_summarize_emails.invoke({
            "placeholder": ""
        })
        return {"result": result}
    except Exception as e:
        logger.error(f"Error in processing: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/emails/stats")
async def get_email_stats():
    """Statistiques sur les emails traités"""
    return {
        "total_processed": len(processed_emails),
        "stored_summaries": email_collection.count(),
        "last_processed": datetime.now().isoformat()
    }


if __name__ == "__main__":
    logger.info("🚀 Starting AI Assistant API with email processing and audio transcription...")
    logger.info(f"📊 ChromaDB contains {knowledge_collection.count()} chunks")
    logger.info(f"📧 {len(processed_emails)} emails have been processed")
    uvicorn.run(app, host="0.0.0.0", port=8000)