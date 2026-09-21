"""
Agent IA avec LangGraph et FastAPI
Application principale
"""
import logging
from sap_tools import sap_tools_list
from email_reply_validation import email_reply_tools
from fastapi import File, UploadFile
import time
import schedule
import threading
from typing import Optional, List
from datetime import datetime
from pydantic import BaseModel

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from langchain_core.messages import HumanMessage, AIMessage
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent

from config import (
    logger, DEEPSEEK_API_KEY, knowledge_collection,
    email_collection, processed_emails_file, processed_drive_files_file
)
from emails import (
    process_and_summarize_emails, search_emails, summarize_email,
    load_processed_emails, fetch_and_store_recent_emails
)
from drive import (
    list_drive_files, sync_drive_tool, summarize_drive_document,
    sync_google_drive, fetch_and_store_recent_drive_files,
    load_processed_drive_files
)
from transcription import transcribe_audio_file
from google_calendar import calendar_tools

# --- CONFIGURATION FASTAPI ---
app = FastAPI()

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- TOOL: RECHERCHE DANS LA BASE DE CONNAISSANCES ---
from langchain_core.tools import tool
from meeting_tools import meeting_tools_list, list_upcoming_deadlines, list_overdue_actions, prepare_meeting


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

            source_type = metadata.get("type", "unknown")
            filename = metadata.get("filename", metadata.get("subject", "Sans titre"))
            sender = metadata.get("sender", "")

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
        logger.error(f"Error searching knowledge: {e}", exc_info=True)
        return f"Erreur lors de la recherche: {str(e)}"


# --- INITIALISATION DE L'AGENT AVEC TOUS LES OUTILS ---
tools = [
            process_and_summarize_emails,
            search_emails,
            summarize_email,
            list_drive_files,
            sync_drive_tool,
            summarize_drive_document,
            search_knowledge,
        ] + calendar_tools + meeting_tools_list + sap_tools_list
tools.extend(email_reply_tools)
# Initialize the LLM
llm = ChatOpenAI(
    model="deepseek-v4-flash",
    temperature=0.7,
    api_key=DEEPSEEK_API_KEY,
    base_url="https://api.deepseek.com"
)

# Create agent
try:
    agent = create_react_agent(
        model=llm,
        tools=tools,
        prompt="""Tu es un assistant IA utile avec accès aux outils suivants:

EMAILS:
- process_and_summarize_emails: Traiter et résumer les nouveaux emails
- search_emails: Rechercher dans les emails
- summarize_email: Résumer un email spécifique

EMAIL_REPLY_VALIDATION:
-  prepare_email_reply: L'agent prépare une réponse
- La réponse est enregistrée avec status='pending'. Rien n'est envoyé jusqu'à ce que l'utilisateur valide la réponse
- envoi une réponse

GOOGLE DRIVE:
- list_drive_files: Lister les fichiers accessibles dans Google Drive
- sync_drive_tool: Synchroniser Google Drive vers ChromaDB
- summarize_drive_document: Résumer un document Drive

GOOGLE CALENDAR:
- get_today_events: Voir les réunions d'aujourd'hui
- get_week_events: Voir les réunions de la semaine
- count_week_events: Compter les réunions de la semaine
- get_next_meeting: Voir la prochaine réunion
- search_calendar: Rechercher une réunion
- create_calendar_event: Créer une réunion
- update_calendar_event: Modifier une réunion
- delete_calendar_event: Supprimer une réunion

GESTION DES RÉUNIONS:  # <<<< AJOUTER CETTE SECTION
- prepare_meeting: Préparer une réunion (agenda, contexte, points critiques)
  Utilise cette fonction pour: "prépare la réunion de [type] du [date]"
  Exemple: prepare_meeting(meeting_type="commercial", meeting_date="2024-10-19")
  
- get_meeting_facilitator_suggestion: Suggérer un animateur basé sur les participants
  Utilise pour: "qui devrait animer?"
  Exemple: get_meeting_facilitator_suggestion(participants_emails="jean@ex.com,marie@ex.com")
  
- analyze_meeting_transcript: Analyser une transcription/notes de réunion
  Extrait participants, points, décisions, actions
  Utilise pour: "analyse cette transcription: [texte]"
  Exemple: analyze_meeting_transcript(transcript="...", meeting_type="strategique", participants_emails="...")
  
- generate_meeting_minutes: Générer un procès-verbal complet et professionnel
  Crée un PV structuré en HTML avec points, décisions, plan d'action
  Utilise pour: "génère le PV de la réunion"
  Exemple: generate_meeting_minutes(meeting_id="...", title="...", meeting_type="...", facilitator="...", ...)
  
- check_action_status: Vérifier le statut d'une action spécifique
  Utilise pour: "où en est l'action X?"
  Exemple: check_action_status(action_id="act_1")
  
- update_action: Mettre à jour le statut d'une action
  Statuts: assignée, en_cours, bloquée, complétée, en_retard
  Utilise pour: "mets à jour l'action X à en_cours"
  Exemple: update_action(action_id="act_1", new_status="en_cours", notes="Commencé hier")
  
- list_upcoming_deadlines: Lister les actions urgentes dans les N prochains jours
  Utilise pour: "mes actions urgentes?" ou "deadlines prochains 7 jours"
  Exemple: list_upcoming_deadlines(days_ahead=7)
  
- list_overdue_actions: Lister les actions en retard
  Utilise pour: "y a-t-il des actions en retard?" ou "actions bloquées?"
  Exemple: list_overdue_actions()
 SAP BUSINESS ONE (ERP / données financières & commerciales):
- get_sap_snapshot: Vue globale (CA, marge, top clients, retards)
  Utilise pour: "où en est le business?", "résumé SAP", "snapshot commercial"
  Exemple: get_sap_snapshot(period="current_month")

- get_sap_revenue: Chiffre d'affaires sur une période avec regroupement
  Utilise pour: "quel est le CA ce mois?", "CA par région", "CA par commercial"
  Exemple: get_sap_revenue(period="current_month", group_by="Region")

- get_sap_sales_performance: Réalisations commerciales vs objectifs
  Utilise pour: "est-ce qu'on atteint les objectifs?", "performance de Karim"
  Exemple: get_sap_sales_performance(period="current_month", sales_owner="Karim")

- get_sap_margins: Analyse des marges par catégorie/produit/région
  Utilise pour: "quelles sont les marges?", "produits les plus rentables?"
  Exemple: get_sap_margins(period="last_30d", group_by="Category")

- get_sap_delays_and_gaps: Retards de paiement/livraison et écarts
  Utilise pour: "y a-t-il des retards?", "clients en retard de paiement?"
  Exemple: get_sap_delays_and_gaps(period="last_30d", threshold_days=5)

- link_decision_to_impact: Relie une décision de réunion à son impact SAP réel
  Utilise pour: "quel est l'impact de la décision X?", "est-ce que la décision a marché?"
  Exemple: link_decision_to_impact(decision_text="focus sur les grands comptes à Casablanca", impact_period="last_30d")

- analyze_objective_gap: Identifie les écarts objectifs vs résultats
  Utilise pour: "où sont les écarts?", "qui est en retard sur ses objectifs?"
  Exemple: analyze_objective_gap(period="current_month", dimension="SalesOwner")

RÈGLES SAP IMPORTANTES:
- Toujours croiser décisions de réunion (meeting_tools) avec link_decision_to_impact
- Pour un reporting complet: get_sap_snapshot + analyze_objective_gap + get_sap_delays_and_gaps
- Périodes acceptées: "current_month", "last_30d", "YYYY-MM", "YYYY-MM-DD:YYYY-MM-DD"
- Les données SAP sont actuellement MOCK (démo) — le branchement réel passera par SAP Service Layer
CONNAISSANCES:
- search_knowledge: Rechercher dans la base de connaissances (emails + Drive + transcriptions + réunions)
 
RÈGLES IMPORTANTES RÉUNIONS:
- Pour préparer une réunion: utilise prepare_meeting avec le type et la date
- Types: "commercial", "marketing", "financier", "strategique", "hebdomadaire", "bihebdomadaire", "ponctuelle"
- Pour analyser une transcription: utilise analyze_meeting_transcript puis generate_meeting_minutes
- Pour générer un PV: utilise generate_meeting_minutes avec tous les détails (titre, type, participants, decisions, actions)
- Pour tracker: utilise check_action_status et update_action
- Priorités d'action: "court_terme", "moyen_terme", "long_terme"
- Statuts: "assignée", "en_cours", "bloquée", "complétée", "en_retard"
 
RÈGLES IMPORTANTES EXISTANTES:
- Pour "qu'ai-je aujourd'hui?", utilise get_today_events
- Pour "combien de réunions cette semaine?", utilise count_week_events
- Pour créer une réunion, utilise create_calendar_event avec dates ISO8601
- Pour "liste mes fichiers Drive", utilise list_drive_files
- Pour rechercher le contenu indexé, utilise search_knowledge
        """
    )
    logger.info("✅ Agent created successfully with email, drive, calendar and knowledge tools")
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


class KnowledgeQuery(BaseModel):
    query: str
    limit: Optional[int] = 5


class EmailQuery(BaseModel):
    query: str
    limit: Optional[int] = 5


# --- SCHEDULER ---
def scheduled_email_processing():
    """Fonction exécutée par le scheduler toutes les heures"""
    logger.info("⏰ Running scheduled email processing...")
    result = process_and_summarize_emails.invoke({"placeholder": ""})
    logger.info(f"📊 Scheduled result: {result}")
    return result


def start_scheduler():
    """Démarre le scheduler dans un thread background"""
    schedule.every().hour.do(scheduled_email_processing)
    logger.info("🔄 Scheduler started - emails will be processed every hour")

    while True:
        schedule.run_pending()
        time.sleep(60)


# Lancer le scheduler dans un thread daemon
scheduler_thread = threading.Thread(target=start_scheduler, daemon=True)
scheduler_thread.start()


# --- ENDPOINTS ---
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

        return ChatResponse(
            response=final_response,
            tool_calls=tool_calls
        )

    except Exception as e:
        logger.error(f"Error in chat: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# --- TRANSCRIPTION AUDIO ENDPOINT ---


@app.post("/transcribe")
async def transcribe_audio(file: UploadFile = File(...)):
    """
    Reçoit un fichier audio (multipart/form-data, champ 'file'),
    le transcrit via AssemblyAI et renvoie le texte.
    """
    try:
        if not file:
            raise HTTPException(status_code=400, detail="Aucun fichier fourni")

        content = await file.read()
        logger.info(f"📥 Received audio: {file.filename} ({len(content)} bytes)")

        if len(content) == 0:
            raise HTTPException(status_code=400, detail="Fichier vide")

        text = transcribe_audio_file(content, file.filename or "audio.wav")

        return {
            "text": text,
            "transcript": text,
            "filename": file.filename,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Transcribe endpoint error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

# --- EMAILS ENDPOINTS ---
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
        result = process_and_summarize_emails.invoke({"placeholder": ""})
        return {"result": result}
    except Exception as e:
        logger.error(f"Error in processing: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/emails/stats")
async def get_email_stats():
    """Statistiques sur les emails traités"""
    processed_emails = load_processed_emails()
    return {
        "total_processed": len(processed_emails),
        "stored_summaries": email_collection.count(),
        "last_processed": datetime.now().isoformat()
    }


@app.post("/api/fetch-recent-emails")
async def fetch_recent_emails_endpoint(max_emails: int = 10):
    """
    Récupère les N derniers mails et les stocke en BD
    """
    try:
        result = fetch_and_store_recent_emails(max_emails)
        return result
    except Exception as e:
        logger.error(f"Error in endpoint: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


# --- DRIVE ENDPOINTS ---
@app.get("/drive/list")
async def list_drive_endpoint():
    """Endpoint pour lister les fichiers Drive"""
    try:
        result = list_drive_files.invoke({"page_size": 100})
        return {"files": result}
    except Exception as e:
        logger.error(f"Drive listing error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/drive/sync")
async def sync_drive():
    """Synchronise Google Drive -> extraction/transcription -> ChromaDB."""
    try:
        result = sync_google_drive()
        return {
            "status": "success",
            **result
        }
    except Exception as e:
        logger.error(f"Drive sync error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/fetch-recent-drive-files")
async def fetch_recent_drive_files_endpoint(max_files: int = 4):
    """
    Récupère les N derniers fichiers Drive et les stocke en BD
    """
    try:
        result = fetch_and_store_recent_drive_files(max_files)
        return result
    except Exception as e:
        logger.error(f"Error in endpoint: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


# --- KNOWLEDGE BASE ENDPOINTS ---
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
                "metadata": metadatas[i] if i < len(metadatas) else {},
                "distance": distances[i] if i < len(distances) else None
            })

        return {
            "query": query.query,
            "count": len(output),
            "results": output
        }

    except Exception as e:
        logger.error(f"Knowledge search error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/knowledge/stats")
async def knowledge_stats():
    """Statistiques de la base de connaissances."""
    try:
        processed_emails = load_processed_emails()
        processed_drive_files = load_processed_drive_files()
        total = knowledge_collection.count()

        return {
            "collection": "email_summaries",
            "total_chunks": total,
            "emails_processed": len(processed_emails),
            "drive_files_processed": len(processed_drive_files)
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/sync/status")
async def get_sync_status():
    """Statistiques complètes de synchronisation"""
    processed_emails = load_processed_emails()
    processed_drive_files = load_processed_drive_files()

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


@app.get("/api/storage-stats")
async def get_storage_stats():
    """Statistiques de la base de données"""
    processed_emails = load_processed_emails()
    processed_drive_files = load_processed_drive_files()
    total_records = email_collection.count()

    return {
        "total_stored": total_records,
        "emails_processed": len(processed_emails),
        "drive_files_processed": len(processed_drive_files),
        "storage": {
            "type": "ChromaDB",
            "path": "./email_vector_db"
        },
        "last_updated": datetime.now().isoformat()
    }


@app.post("/api/fetch-all")
async def fetch_all_endpoint(emails_count: int = 10, files_count: int = 4):
    """
    Récupère et stocke TOUT en une seule requête
    """
    try:
        emails_result = fetch_and_store_recent_emails(emails_count)
        files_result = fetch_and_store_recent_drive_files(files_count)

        return {
            "status": "success",
            "emails": emails_result,
            "drive_files": files_result,
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        logger.error(f"Error in combined fetch: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


# --- CALENDAR ENDPOINTS ---
@app.get("/calendar/today")
async def get_today_calendar():
    """Récupère les réunions d'aujourd'hui"""
    try:
        from google_calendar import get_today_events
        result = get_today_events.invoke({})
        return {"events": result}
    except Exception as e:
        logger.error(f"Error getting today events: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/calendar/week")
async def get_week_calendar():
    """Récupère les réunions de la semaine"""
    try:
        from google_calendar import get_week_events
        result = get_week_events.invoke({})
        return {"events": result}
    except Exception as e:
        logger.error(f"Error getting week events: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/calendar/next")
async def get_next_calendar():
    """Récupère la prochaine réunion"""
    try:
        from google_calendar import get_next_meeting
        result = get_next_meeting.invoke({})
        return {"event": result}
    except Exception as e:
        logger.error(f"Error getting next event: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/calendar/count-week")
async def count_week_calendar():
    """Compte les réunions de la semaine"""
    try:
        from google_calendar import count_week_events
        result = count_week_events.invoke({})
        return {"count": result}
    except Exception as e:
        logger.error(f"Error counting week events: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


class CalendarSearchRequest(BaseModel):
    query: str


@app.post("/calendar/search")
async def search_calendar_endpoint(request: CalendarSearchRequest):
    """Recherche une réunion"""
    try:
        from google_calendar import search_calendar
        result = search_calendar.invoke({"search_text": request.query})
        return {"results": result}
    except Exception as e:
        logger.error(f"Error searching calendar: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


class CalendarEventRequest(BaseModel):
    summary: str
    start: str
    end: str
    description: Optional[str] = ""
    location: Optional[str] = ""


@app.post("/calendar/create")
async def create_calendar_event_endpoint(request: CalendarEventRequest):
    """Crée une réunion"""
    try:
        from google_calendar import create_calendar_event
        result = create_calendar_event.invoke({
            "summary": request.summary,
            "start": request.start,
            "end": request.end,
            "description": request.description or "",
            "location": request.location or ""
        })
        return {"result": result}
    except Exception as e:
        logger.error(f"Error creating calendar event: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


class CalendarUpdateRequest(BaseModel):
    event_id: str
    summary: Optional[str] = None
    location: Optional[str] = None
    description: Optional[str] = None


@app.post("/calendar/update")
async def update_calendar_event_endpoint(request: CalendarUpdateRequest):
    """Met à jour une réunion"""
    try:
        from google_calendar import update_calendar_event
        result = update_calendar_event.invoke({
            "event_id": request.event_id,
            "summary": request.summary,
            "location": request.location,
            "description": request.description
        })
        return {"result": result}
    except Exception as e:
        logger.error(f"Error updating calendar event: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


class CalendarDeleteRequest(BaseModel):
    event_id: str


@app.post("/calendar/delete")
async def delete_calendar_event_endpoint(request: CalendarDeleteRequest):
    """Supprime une réunion"""
    try:
        from google_calendar import delete_calendar_event
        result = delete_calendar_event.invoke({"event_id": request.event_id})
        return {"result": result}
    except Exception as e:
        logger.error(f"Error deleting calendar event: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


class MeetingPrepRequest(BaseModel):
    meeting_type: str
    meeting_date: str
    title: Optional[str] = None


@app.post("/meetings/prepare")
async def prepare_meeting_endpoint(request: MeetingPrepRequest):
    """Endpoint pour préparer une réunion"""
    try:
        result = prepare_meeting.invoke({
            "meeting_type": request.meeting_type,
            "meeting_date": request.meeting_date,
            "meeting_title": request.title
        })
        return {"preparation": result}
    except Exception as e:
        logger.error(f"Error preparing meeting: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/meetings/deadlines")
async def get_deadlines_endpoint(days_ahead: int = 7):
    """Endpoint pour les deadlines"""
    try:
        result = list_upcoming_deadlines.invoke({"days_ahead": days_ahead})
        return {"deadlines": result}
    except Exception as e:
        logger.error(f"Error getting deadlines: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/meetings/overdue")
async def get_overdue_endpoint():
    """Endpoint pour les actions en retard"""
    try:
        result = list_overdue_actions.invoke({})
        return {"overdue_actions": result}
    except Exception as e:
        logger.error(f"Error getting overdue actions: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
# --- SAP ENDPOINTS ---
class SAPPeriodRequest(BaseModel):
    period: str = "current_month"


class SAPRevenueRequest(BaseModel):
    period: str = "current_month"
    group_by: str = "none"


class SAPPerformanceRequest(BaseModel):
    period: str = "current_month"
    sales_owner: Optional[str] = None


class SAPMarginsRequest(BaseModel):
    period: str = "current_month"
    group_by: str = "Category"
    min_margin_pct: float = 0.0


class SAPDelaysRequest(BaseModel):
    period: str = "last_30d"
    threshold_days: int = 5


class SAPDecisionImpactRequest(BaseModel):
    decision_text: str
    impact_period: str = "last_30d"


class SAPGapRequest(BaseModel):
    period: str = "current_month"
    dimension: str = "SalesOwner"


@app.get("/sap/snapshot")
async def sap_snapshot_endpoint(period: str = "current_month"):
    """Vue globale SAP B1."""
    from sap_tools import get_sap_snapshot
    return {"result": get_sap_snapshot.invoke({"period": period})}


@app.post("/sap/revenue")
async def sap_revenue_endpoint(req: SAPRevenueRequest):
    """CA SAP B1."""
    from sap_tools import get_sap_revenue
    return {"result": get_sap_revenue.invoke({"period": req.period, "group_by": req.group_by})}


@app.post("/sap/performance")
async def sap_performance_endpoint(req: SAPPerformanceRequest):
    """Réalisations commerciales vs objectifs."""
    from sap_tools import get_sap_sales_performance
    return {"result": get_sap_sales_performance.invoke({
        "period": req.period, "sales_owner": req.sales_owner
    })}


@app.post("/sap/margins")
async def sap_margins_endpoint(req: SAPMarginsRequest):
    """Analyse des marges."""
    from sap_tools import get_sap_margins
    return {"result": get_sap_margins.invoke({
        "period": req.period, "group_by": req.group_by, "min_margin_pct": req.min_margin_pct
    })}


@app.post("/sap/delays")
async def sap_delays_endpoint(req: SAPDelaysRequest):
    """Retards et écarts opérationnels."""
    from sap_tools import get_sap_delays_and_gaps
    return {"result": get_sap_delays_and_gaps.invoke({
        "period": req.period, "threshold_days": req.threshold_days
    })}


@app.post("/sap/decision-impact")
async def sap_decision_impact_endpoint(req: SAPDecisionImpactRequest):
    """Impact réel d'une décision de réunion."""
    from sap_tools import link_decision_to_impact
    return {"result": link_decision_to_impact.invoke({
        "decision_text": req.decision_text, "impact_period": req.impact_period
    })}


@app.post("/sap/objective-gap")
async def sap_objective_gap_endpoint(req: SAPGapRequest):
    """Écarts objectifs vs résultats."""
    from sap_tools import analyze_objective_gap
    return {"result": analyze_objective_gap.invoke({
        "period": req.period, "dimension": req.dimension
    })}
if __name__ == "__main__":
    logger.info("🚀 Starting AI Assistant API with email processing and audio transcription...")
    logger.info(f"📊 ChromaDB contains {knowledge_collection.count()} chunks")

    processed_emails = load_processed_emails()
    logger.info(f"📧 {len(processed_emails)} emails have been processed")

    uvicorn.run(app, host="0.0.0.0", port=8000)
