"""
Module de gestion des emails Gmail
Récupération, résumé et indexation dans ChromaDB
"""
import json
import logging
import os
import pickle
import base64
from typing import List, Dict, Any
from datetime import datetime

from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from openai import OpenAI
from langchain_core.tools import tool

from config import (
    logger, GMAIL_SCOPES, GMAIL_TOKEN_FILE, email_collection,
    DEEPSEEK_API_KEY, processed_emails_file,client,model
)

# --- AUTHENTIFICATION GMAIL ---
def authenticate_gmail_service():
    """Authentifie l'utilisateur et retourne le service Gmail."""
    creds = None

    if os.path.exists(GMAIL_TOKEN_FILE):
        with open(GMAIL_TOKEN_FILE, 'rb') as token:
            creds = pickle.load(token)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            logger.info("Refreshing Gmail credentials...")
            creds.refresh(Request())
        else:
            if not os.path.exists("credentials.json"):
                raise FileNotFoundError(
                    "credentials.json not found. "
                    "Please download it from Google Cloud Console."
                )

            flow = InstalledAppFlow.from_client_secrets_file(
                'credentials.json',
                GMAIL_SCOPES
            )
            creds = flow.run_local_server(port=0)

        with open(GMAIL_TOKEN_FILE, 'wb') as token:
            pickle.dump(creds, token)

    return build('gmail', 'v1', credentials=creds)


# --- CHARGEMENT/SAUVEGARDE DES EMAILS TRAITÉS ---
def load_processed_emails() -> set:
    """Charge la liste des IDs d'emails déjà traités"""
    try:
        with open(processed_emails_file, 'r') as f:
            return set(json.load(f))
    except FileNotFoundError:
        with open(processed_emails_file, 'w') as f:
            json.dump([], f)
        return set()
    except json.JSONDecodeError:
        with open(processed_emails_file, 'w') as f:
            json.dump([], f)
        return set()


def save_processed_emails(processed_ids: set):
    """Sauvegarde la liste des IDs d'emails traités"""
    with open(processed_emails_file, 'w') as f:
        json.dump(list(processed_ids), f)
def fetch_and_store_recent_emails(max_emails: int = 10) -> Dict[str, Any]:
    """
    Récupère les N derniers mails et les stocke en ChromaDB

    Args:
        max_emails: Nombre d'emails à récupérer (défaut: 10)

    Returns:
        Dictionnaire avec stats et liste des emails traités
    """
    logger.info(f"🔄 Fetching and storing last {max_emails} emails from Gmail...")

    processed_emails = load_processed_emails()

    try:
        service = authenticate_gmail_service()

        results = service.users().messages().list(
            userId='me',
            maxResults=max_emails,
            q='is:unread'
        ).execute()

        messages = results.get('messages', [])
        emails_stored = []

        for message in messages:
            msg_id = message['id']

            if msg_id in processed_emails:
                logger.info(f"⏭️ Email {msg_id} already processed")
                continue

            msg = service.users().messages().get(
                userId='me',
                id=msg_id,
                format='full'
            ).execute()

            headers = msg['payload']['headers']
            subject = next((h['value'] for h in headers if h['name'] == 'Subject'), 'Sans sujet')
            sender = next((h['value'] for h in headers if h['name'] == 'From'), 'Inconnu')
            date_str = next((h['value'] for h in headers if h['name'] == 'Date'), datetime.now().isoformat())

            body = ""
            if 'parts' in msg['payload']:
                for part in msg['payload']['parts']:
                    if part['mimeType'] == 'text/plain':
                        data = part['body'].get('data', '')
                        if data:
                            body = base64.urlsafe_b64decode(data).decode('utf-8')
                        break
            else:
                if 'data' in msg['payload']['body']:
                    body = base64.urlsafe_b64decode(msg['payload']['body']['data']).decode('utf-8')

            body = body[:5000] if body else ""

            email_id = f"email_{msg_id}"
            metadata = {
                "type": "email",
                "sender": sender,
                "subject": subject,
                "date": date_str,
                "message_id": msg_id
            }

            email_collection.add(
                documents=[f"📧 {subject}\n\nDe: {sender}\n\n{body}"],
                metadatas=[metadata],
                ids=[email_id]
            )

            logger.info(f"✅ Email stored: {email_id}")

            emails_stored.append({
                "id": msg_id,
                "sender": sender,
                "subject": subject,
                "preview": body[:200] + "..." if len(body) > 200 else body
            })

            processed_emails.add(msg_id)

        save_processed_emails(processed_emails)

        return {
            "status": "success",
            "count": len(emails_stored),
            "emails": emails_stored,
            "chromadb_total": email_collection.count()
        }

    except Exception as e:
        logger.error(f"❌ Error fetching emails: {str(e)}")
        return {
            "status": "error",
            "error": str(e)
        }


# --- TOOLS POUR L'AGENT ---
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

    content_preview = email_content[:5000]

    prompt = f"""Tu es un assistant qui résume des emails de manière concise et professionnelle.

    {f"EXPÉDITEUR: {sender}" if sender else ""}
    {f"SUJET: {subject}" if subject else ""}

    CONTENU DE L'EMAIL:
    {content_preview}

    RÉSUMÉ (3-5 phrases claires et structurées):
    """

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=500
        )
        return response.choices[0].message.content
    except Exception as e:
        logger.error(f"Error summarizing email: {str(e)}")
        return f"Erreur lors du résumé: {str(e)}"


@tool
def get_email_from_gmail(max_emails: int = 10) -> List[Dict[str, Any]]:
    """
    Récupère les derniers emails de la boîte Gmail.

    Args:
        max_emails: Nombre maximum d'emails à récupérer

    Returns:
        Liste d'emails avec leurs métadonnées
    """
    logger.info(f"Fetching last {max_emails} emails from Gmail")

    try:
        service = authenticate_gmail_service()

        result = service.users().messages().list(
            userId='me',
            maxResults=max_emails,
            q='is:unread OR newer_than:1d'
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

            subject = next((h['value'] for h in headers if h['name'] == 'Subject'), 'Sans sujet')
            sender = next((h['value'] for h in headers if h['name'] == 'From'), 'Inconnu')
            date = next((h['value'] for h in headers if h['name'] == 'Date'), '')

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
    Récupère les nouveaux emails de Gmail, les résume et les stocke dans ChromaDB.

    Args:
        placeholder: Paramètre vide pour compatibilité avec l'agent

    Returns:
        Un message indiquant le nombre d'emails traités
    """
    logger.info("Starting email processing and summarization...")

    processed_emails = load_processed_emails()

    try:
        emails = get_email_from_gmail.invoke({"max_emails": 10})

        if isinstance(emails, list) and len(emails) > 0 and "error" in emails[0]:
            return f"Impossible de récupérer les emails: {emails[0]['error']}"

        if not emails:
            return "Aucun email trouvé."

        summaries = []
        processed_count = 0

        for email_data in emails:
            email_id = email_data.get('id', '')

            if email_id in processed_emails:
                logger.info(f"Email {email_id} already processed, skipping...")
                continue

            summary = summarize_email.invoke({
                "email_content": email_data.get('body', ''),
                "sender": email_data.get('from', ''),
                "subject": email_data.get('subject', '')
            })

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

        save_processed_emails(processed_emails)

        if processed_count == 0:
            return "Aucun nouvel email à traiter."

        return f"✅ {processed_count} emails traités et résumés:\n\n" + "\n".join(summaries)

    except Exception as e:
        logger.error(f"Error in email processing: {str(e)}")
        return f"Erreur lors du traitement: {str(e)}"


