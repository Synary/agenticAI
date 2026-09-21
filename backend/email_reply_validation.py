"""
email_reply_validation.py

Gestion séparée de l'envoi des réponses Gmail.

Ce fichier NE LIT PAS les emails.
La réception/lecture reste dans le fichier Gmail existant.

Workflow:
1. L'agent prépare une réponse -> prepare_email_reply(...)
2. La réponse est enregistrée avec status='pending'. Rien n'est envoyé.
3. L'utilisateur écrit explicitement « Je valide ».
4. L'agent appelle validate_and_send_pending_replies(...).
5. Seule cette fonction possède la logique d'envoi Gmail.

Dépendances:
    pip install google-api-python-client google-auth-oauthlib langchain-core openai chromadb

Configuration: voir config.py
    - DEEPSEEK_API_KEY
    - client (OpenAI pointé sur https://api.deepseek.com)
    - model
    - GMAIL_SCOPES
    - GMAIL_TOKEN_FILE
    - email_collection (collection ChromaDB partagée)
"""

from __future__ import annotations
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
import base64
import json
import logging
import os
import pickle
import uuid
from datetime import datetime, timezone
from email.mime.text import MIMEText
from email.utils import parseaddr
from pathlib import Path
from typing import Any, Dict, List, Optional

from googleapiclient.discovery import build
from langchain_core.tools import tool

from config import (
    logger,
    GMAIL_SCOPES,
    GMAIL_TOKEN_FILE,
    email_collection,
    DEEPSEEK_API_KEY,
    client,
    model,
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PENDING_REPLIES_FILE = Path("pending_email_replies.json")

# Seule une validation explicite permet l'envoi.
VALIDATION_PHRASES = {
    "je valide",
    "valide",
    "j valide",
    "envoie",
    "envoyer",
    "tu peux envoyer",
    "vous pouvez envoyer",
    "envoie la réponse",
    "envoie le mail",
    "envoyer la réponse",
    "envoyer le mail",
}

# ---------------------------------------------------------------------------
# Pending replies persistence
# ---------------------------------------------------------------------------


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_pending_replies() -> List[Dict[str, Any]]:
    if not PENDING_REPLIES_FILE.exists():
        return []

    try:
        with PENDING_REPLIES_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)

        if isinstance(data, list):
            return data

        logger.warning("%s ne contient pas une liste JSON.", PENDING_REPLIES_FILE)
        return []
    except (json.JSONDecodeError, OSError) as exc:
        logger.error("Impossible de lire %s: %s", PENDING_REPLIES_FILE, exc)
        return []


def _save_pending_replies(replies: List[Dict[str, Any]]) -> None:
    """Sauvegarde Windows-safe, sans fichier .tmp."""
    try:
        PENDING_REPLIES_FILE.parent.mkdir(parents=True, exist_ok=True)
        with PENDING_REPLIES_FILE.open("w", encoding="utf-8") as f:
            json.dump(replies, f, ensure_ascii=False, indent=2)
            f.flush()
    except PermissionError as exc:
        logger.error("Permission denied: %s", PENDING_REPLIES_FILE)
        raise RuntimeError(
            f"Impossible d'écrire dans '{PENDING_REPLIES_FILE}'. "
            "Vérifiez que le fichier n'est pas ouvert dans un autre programme."
        ) from exc


def _normalize_confirmation(text: str) -> str:
    return " ".join((text or "").strip().lower().split())


def _is_explicit_validation(text: str) -> bool:
    return _normalize_confirmation(text) in VALIDATION_PHRASES


# ---------------------------------------------------------------------------
# Gmail SEND authentication
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Gmail SEND
# ---------------------------------------------------------------------------


def send_gmail_reply(
    recipient: str,
    subject: str,
    body: str,
    *,
    original_message_id: Optional[str] = None,
    thread_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Envoie un email/réponse Gmail.

    Cette fonction est volontairement isolée de toute logique de réception.
    """

    service = authenticate_gmail_service()

    # Accepte aussi bien 'Name <email@example.com>' que 'email@example.com'.
    parsed_name, parsed_email = parseaddr(recipient)
    to_address = parsed_email or recipient.strip()

    if "@" not in to_address:
        raise ValueError(f"Adresse destinataire invalide: {recipient}")

    clean_subject = subject.strip() if subject else ""
    if clean_subject and not clean_subject.lower().startswith("re:"):
        clean_subject = f"Re: {clean_subject}"

    message = MIMEText(body or "", "plain", "utf-8")
    message["To"] = to_address
    message["Subject"] = clean_subject

    # Ces headers permettent à Gmail de rattacher la réponse au message original.
    if original_message_id:
        message["In-Reply-To"] = original_message_id
        message["References"] = original_message_id

    raw = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")
    payload: Dict[str, Any] = {"raw": raw}

    if thread_id:
        payload["threadId"] = thread_id

    result = (
        service.users()
        .messages()
        .send(userId="me", body=payload)
        .execute()
    )

    return {
        "success": True,
        "message_id": result.get("id"),
        "thread_id": result.get("threadId", thread_id),
        "recipient": to_address,
    }


# ---------------------------------------------------------------------------
# Database context (ChromaDB partagée via config.email_collection)
# ---------------------------------------------------------------------------


def search_database_context(query: str, limit: int = 6) -> List[Dict[str, Any]]:
    """Recherche dans la collection ChromaDB commune du projet (config.email_collection)."""

    if email_collection is None:
        logger.warning("email_collection est None (config). Aucun contexte récupéré.")
        return []

    try:
        count = email_collection.count()
    except Exception as exc:
        logger.error("Impossible de lire email_collection: %s", exc)
        return []

    if count == 0:
        return []

    n_results = max(1, min(limit, count, 20))

    results = email_collection.query(
        query_texts=[query],
        n_results=n_results,
        include=["documents", "metadatas", "distances"],
    )

    documents = (results.get("documents") or [[]])[0]
    metadatas = (results.get("metadatas") or [[]])[0]
    distances = (results.get("distances") or [[]])[0]

    contexts: List[Dict[str, Any]] = []

    for i, document in enumerate(documents):
        contexts.append({
            "content": document or "",
            "metadata": (metadatas[i] if i < len(metadatas) else {}) or {},
            "distance": distances[i] if i < len(distances) else None,
        })

    return contexts


def format_database_context(contexts: List[Dict[str, Any]]) -> str:
    """Formate les résultats ChromaDB avec leurs métadonnées métier."""

    if not contexts:
        return "Aucune information pertinente trouvée dans la base."

    blocks = []

    for i, item in enumerate(contexts, 1):
        metadata = item.get("metadata", {}) or {}

        blocks.append(
            f"--- SOURCE {i} ---\n"
            f"type: {metadata.get('type', 'unknown')}\n"
            f"sender: {metadata.get('sender', '')}\n"
            f"subject: {metadata.get('subject', '')}\n"
            f"filename: {metadata.get('filename', '')}\n"
            f"category: {metadata.get('category', '')}\n"
            f"file_id: {metadata.get('file_id', '')}\n"
            f"modified_time: {metadata.get('modified_time', '')}\n"
            f"content:\n{item.get('content', '')[:3500]}"
        )

    return "\n\n".join(blocks)


# ---------------------------------------------------------------------------
# Reply generation
# ---------------------------------------------------------------------------


def generate_reply(
    email_subject: str,
    email_sender: str,
    email_body: str,
    user_instruction: str = "",
) -> str:
    """
    Génère la réponse à partir de l'email ET des données de la base
    vectorielle partagée (config.email_collection).
    """

    if not DEEPSEEK_API_KEY:
        raise RuntimeError("DEEPSEEK_API_KEY n'est pas défini dans config.py")

    if client is None:
        raise RuntimeError("Le client DeepSeek (config.client) n'est pas initialisé.")

    query = (
        f"Demande utilisateur: {user_instruction}\n"
        f"Objet: {email_subject}\n"
        f"Expéditeur: {email_sender}\n"
        f"Email: {email_body[:3500]}"
    )

    contexts = search_database_context(query, limit=6)
    database_context = format_database_context(contexts)

    prompt = f"""
Tu es l'assistant email d'une entreprise.

Tu dois rédiger UNE réponse professionnelle à l'email reçu.

La réponse doit utiliser:
1. la demande de l'utilisateur;
2. le contenu réel de l'email;
3. les informations pertinentes présentes dans la base de connaissances.

RÈGLES IMPORTANTES:
- N'invente aucune donnée.
- Si une information métier n'est pas confirmée par le contexte,
  ne prétends pas qu'elle est confirmée.
- Une information explicitement donnée par l'utilisateur peut être
  transmise dans la réponse.
- Utilise les données de la base lorsqu'elles permettent de préciser,
  confirmer ou contextualiser la réponse.
- Ne mentionne jamais ChromaDB, embeddings, outils, base vectorielle
  ou architecture interne dans l'email.
- Réponse naturelle, professionnelle et concise.
- Réponds dans la langue de l'email/demande.
- Retourne uniquement le corps du mail.

DEMANDE UTILISATEUR:
{user_instruction}

EMAIL:
Expéditeur: {email_sender}
Objet: {email_subject}

{email_body[:5000]}

DONNÉES MÉTIER RÉCUPÉRÉES DE LA BASE:
{database_context}

CORPS DE LA RÉPONSE:
"""

    response = client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "system",
                "content": (
                    "Tu rédiges des emails professionnels "
                    "fondés sur des données fournies."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        temperature=0.1,
    )

    body = (response.choices[0].message.content or "").strip()

    if not body:
        raise RuntimeError("Le modèle n'a retourné aucune réponse.")

    return body


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@tool
def prepare_email_reply(
    email_id: str,
    recipient: str,
    subject: str,
    email_body: str,
    user_instruction: str,
    thread_id: str = "",
    original_message_id: str = "",
) -> str:
    """Prépare une réponse et la met en attente SANS l'envoyer.

    À utiliser lorsque l'agent veut proposer une réponse à l'utilisateur.
    """

    if not email_id.strip():
        raise ValueError("email_id est obligatoire")
    if not recipient.strip():
        raise ValueError("recipient est obligatoire")
    if not email_body.strip():
        raise ValueError("email_body est obligatoire")
    if not user_instruction.strip():
        raise ValueError("user_instruction est obligatoire")

    body = generate_reply(
        email_subject=subject,
        email_sender=recipient,
        email_body=email_body,
        user_instruction=user_instruction,
    )

    replies = _load_pending_replies()

    # Remplace une ancienne proposition pending pour le même email.
    replies = [
        item
        for item in replies
        if not (
            item.get("email_id") == email_id
            and item.get("status") == "pending"
        )
    ]

    reply = {
        "id": str(uuid.uuid4()),
        "email_id": email_id,
        "thread_id": thread_id.strip(),
        "original_message_id": original_message_id.strip() or email_id.strip(),
        "recipient": recipient.strip(),
        "subject": subject.strip(),
        "body": body.strip(),
        "status": "pending",
        "created_at": _utc_now(),
        "sent_at": None,
        "gmail_message_id": None,
    }

    replies.append(reply)
    _save_pending_replies(replies)

    return (
        "Réponse préparée et enregistrée en attente de validation. "
        "Aucun email n'a été envoyé.\n\n"
        f"Destinataire: {reply['recipient']}\n"
        f"Objet: {reply['subject']}\n"
        f"Réponse:\n{reply['body']}"
    )


@tool
def get_pending_email_replies() -> str:
    """Retourne les réponses actuellement en attente de validation."""

    replies = [r for r in _load_pending_replies() if r.get("status") == "pending"]

    if not replies:
        return "Aucune réponse email en attente de validation."

    result = []
    for index, reply in enumerate(replies, start=1):
        result.append(
            f"[{index}] email_id={reply.get('email_id')}\n"
            f"Destinataire: {reply.get('recipient')}\n"
            f"Objet: {reply.get('subject')}\n"
            f"Réponse:\n{reply.get('body')}"
        )

    return "\n\n---\n\n".join(result)


@tool
def validate_and_send_pending_replies(
    confirmation: str,
    email_id: str = "",
) -> str:
    """Envoie une réponse UNIQUEMENT après une validation explicite.

    Exemple attendu depuis le chatbot:
        confirmation='Je valide'

    Si email_id est vide, la dernière réponse pending est envoyée.
    """

    if not _is_explicit_validation(confirmation):
        return (
            "Envoi refusé: confirmation explicite requise. "
            "Écrivez par exemple « Je valide ». Aucun email n'a été envoyé."
        )

    replies = _load_pending_replies()
    pending = [r for r in replies if r.get("status") == "pending"]

    if not pending:
        return "Aucune réponse en attente. Aucun email n'a été envoyé."

    if email_id.strip():
        candidates = [r for r in pending if r.get("email_id") == email_id.strip()]
        if not candidates:
            return (
                f"Aucune réponse pending trouvée pour email_id={email_id}. "
                "Aucun email n'a été envoyé."
            )
        target = candidates[0]
    else:
        # Sans ID, on envoie uniquement la plus récente, jamais toutes les réponses.
        target = max(pending, key=lambda r: r.get("created_at", ""))

    try:
        result = send_gmail_reply(
            target["recipient"],
            target.get("subject", ""),
            target.get("body", ""),
            original_message_id=target.get("original_message_id") or target.get("email_id"),
            thread_id=target.get("thread_id") or None,
        )
    except Exception as exc:
        logger.exception("Échec de l'envoi Gmail")
        return (
            f"Échec de l'envoi de la réponse à {target.get('recipient')}: {exc}\n"
            "La réponse reste en attente et pourra être réessayée."
        )

    target["status"] = "sent"
    target["sent_at"] = _utc_now()
    target["gmail_message_id"] = result.get("message_id")
    _save_pending_replies(replies)

    return (
        "Email envoyé avec succès.\n"
        f"Destinataire: {target.get('recipient')}\n"
        f"Objet: {target.get('subject')}\n"
        f"Gmail message ID: {result.get('message_id')}"
    )

@tool
def cancel_pending_email_reply(email_id: str) -> str:
    """Annule une réponse pending sans envoyer d'email."""

    if not email_id.strip():
        return "email_id est obligatoire pour annuler une réponse."

    replies = _load_pending_replies()
    found = False

    for reply in replies:
        if reply.get("email_id") == email_id.strip() and reply.get("status") == "pending":
            reply["status"] = "cancelled"
            reply["cancelled_at"] = _utc_now()
            found = True

    if not found:
        return f"Aucune réponse pending trouvée pour email_id={email_id}."

    _save_pending_replies(replies)
    return f"Réponse annulée pour email_id={email_id}. Aucun email n'a été envoyé."


# ---------------------------------------------------------------------------
# Export à ajouter à l'agent
# ---------------------------------------------------------------------------

email_reply_tools = [
    prepare_email_reply,
    get_pending_email_replies,
    validate_and_send_pending_replies,
    cancel_pending_email_reply,
]


__all__ = [
    "prepare_email_reply",
    "get_pending_email_replies",
    "validate_and_send_pending_replies",
    "cancel_pending_email_reply",
    "email_reply_tools",
    "send_gmail_reply",
    "authenticate_gmail_service",
    "search_database_context",
    "format_database_context",
    "generate_reply",
]