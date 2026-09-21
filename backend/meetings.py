"""
Module de gestion complète des réunions
- Préparation (ordre du jour, contexte)
- Capture (participants, décisions, actions)
- PV automatique
- Suivi des actions
"""
import json
import logging
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from enum import Enum
from pydantic import BaseModel

logger = logging.getLogger(__name__)

# --- ÉNUMÉRATIONS ---
class MeetingType(str, Enum):
    """Types de réunion définis"""
    COMMERCIAL = "commercial"      # Responsables zones, grands comptes
    MARKETING = "marketing"
    FINANCIAL = "financier"        # Comptabilité, contrôle de gestion
    STRATEGIC = "strategique"      # Groupe restreint 6-7 personnes
    WEEKLY = "hebdomadaire"
    BIWEEKLY = "bihebdomadaire"
    PUNCTUAL = "ponctuelle"        # Projets spécifiques

class ActionPriority(str, Enum):
    """Priorité des actions"""
    SHORT_TERM = "court_terme"
    MID_TERM = "moyen_terme"
    LONG_TERM = "long_terme"

class ActionStatus(str, Enum):
    """Statut d'une action"""
    ASSIGNED = "assignée"
    IN_PROGRESS = "en_cours"
    BLOCKED = "bloquée"
    COMPLETED = "complétée"
    OVERDUE = "en_retard"

# --- MODÈLES PYDANTIC ---
class Action(BaseModel):
    """Modèle pour une action du plan"""
    id: str
    description: str
    owner: str                      # Responsable
    deadline: str                   # Date format ISO
    priority: ActionPriority
    status: ActionStatus = ActionStatus.ASSIGNED
    kpi: Optional[str] = None       # Indicateur de suivi
    meeting_id: str                 # Référence à la réunion

class Decision(BaseModel):
    """Modèle pour une décision"""
    id: str
    description: str
    impact: Optional[str] = None
    requires_action: bool = False
    meeting_id: str

class Participant(BaseModel):
    """Participant à une réunion"""
    email: str
    name: str
    department: Optional[str] = None
    role: Optional[str] = None

class MeetingPrepContext(BaseModel):
    """Contexte de préparation d'une réunion"""
    meeting_id: str
    meeting_type: MeetingType
    date: str
    previous_meeting_id: Optional[str] = None
    previous_decisions: List[Decision] = []
    previous_actions: List[Action] = []
    action_status_summary: Dict[str, int] = {}  # {status: count}

class MeetingMinutes(BaseModel):
    """Procès-verbal structuré"""
    meeting_id: str
    date: str
    meeting_type: MeetingType
    title: str
    facilitator: str
    participants: List[Participant]
    
    # Contenu
    topics_discussed: List[str]     # Points abordés
    decisions: List[Decision]
    action_plan: List[Action]
    
    # Métadonnées
    duration_minutes: Optional[int] = None
    next_meeting_date: Optional[str] = None
    notes: Optional[str] = None
    created_at: str = None
    
    def __init__(self, **data):
        if 'created_at' not in data or data['created_at'] is None:
            data['created_at'] = datetime.now().isoformat()
        super().__init__(**data)

# --- CLASSE PRINCIPALE ---
class MeetingManager:
    """Gestionnaire complet de réunions"""
    
    def __init__(self, chroma_db_meetings):
        """
        Initialise le gestionnaire avec collections ChromaDB
        """
        self.meetings_collection = chroma_db_meetings
        self.logger = logger
    
    # ============ PRÉPARATION ============
    
    def get_meeting_prep_context(
        self, 
        meeting_type: MeetingType,
        meeting_date: str
    ) -> MeetingPrepContext:
        """
        Récupère le contexte de la réunion précédente pour la préparation
        """
        # Chercher la dernière réunion du même type
        results = self.meetings_collection.query(
            query_texts=[f"type:{meeting_type.value}"],
            n_results=1
        )
        
        previous_meeting_id = None
        previous_decisions = []
        previous_actions = []
        action_status_summary = {}
        
        if results.get("documents") and results["documents"][0]:
            try:
                prev_data = json.loads(results["documents"][0][0])
                previous_meeting_id = prev_data.get("meeting_id")
                
                # Charger les décisions et actions précédentes
                previous_decisions = [
                    Decision(**d) for d in prev_data.get("decisions", [])
                ]
                previous_actions = [
                    Action(**a) for a in prev_data.get("action_plan", [])
                ]
                
                # Résumer les statuts
                for action in previous_actions:
                    status = action.status.value
                    action_status_summary[status] = action_status_summary.get(status, 0) + 1
            except Exception as e:
                self.logger.error(f"Erreur chargement contexte: {e}")
        
        return MeetingPrepContext(
            meeting_id=f"meeting_{datetime.now().timestamp()}",
            meeting_type=meeting_type,
            date=meeting_date,
            previous_meeting_id=previous_meeting_id,
            previous_decisions=previous_decisions,
            previous_actions=previous_actions,
            action_status_summary=action_status_summary
        )
    
    def suggest_agenda(
        self,
        meeting_type: MeetingType,
        prep_context: MeetingPrepContext,
        duration_minutes: int = 60
    ) -> Dict[str, Any]:
        """
        Propose un ordre du jour structuré selon le type de réunion
        """
        agendas = {
            MeetingType.COMMERCIAL: [
                ("Synthèse des objectifs commerciaux", 5),
                ("Progression par zone/client", 20),
                ("Obstacles et ressources", 15),
                ("Décisions et prochaines étapes", 10),
                ("Questions ouvertes", 10),
            ],
            MeetingType.MARKETING: [
                ("Récapitulatif des campagnes en cours", 10),
                ("Performances et KPIs", 15),
                ("Ajustements stratégiques", 15),
                ("Calendrier à venir", 10),
                ("Discussion libre", 10),
            ],
            MeetingType.FINANCIAL: [
                ("État financier du mois/trimestre", 15),
                ("Écarts vs budget", 15),
                ("Ratios et indicateurs clés", 10),
                ("Actions correctives", 15),
                ("Questions et discussion", 5),
            ],
            MeetingType.STRATEGIC: [
                ("Vision et contexte stratégique", 10),
                ("Initiatives prioritaires", 15),
                ("Ressources et alignement", 10),
                ("Risques et opportunités", 10),
                ("Décisions critiques", 10),
                ("Suivi et communication", 5),
            ],
            MeetingType.WEEKLY: [
                ("Points clés de la semaine", 10),
                ("Statut des projets", 20),
                ("Blocages et supports", 10),
                ("Prochaines priorités", 10),
                ("Divers", 10),
            ],
            MeetingType.PUNCTUAL: [
                ("Contexte et objectif", 5),
                ("Enjeux et périmètre", 10),
                ("Contenu technique/métier", 30),
                ("Prochaines étapes", 10),
                ("Questions", 5),
            ]
        }
        
        items = agendas.get(meeting_type, agendas[MeetingType.WEEKLY])
        
        agenda = {
            "type": meeting_type.value,
            "duration_total": duration_minutes,
            "items": [
                {
                    "order": i + 1,
                    "topic": topic,
                    "duration_minutes": duration,
                    "notes": ""
                }
                for i, (topic, duration) in enumerate(items)
            ]
        }
        
        # Ajouter un item rappel des actions précédentes si applicable
        if prep_context.previous_actions:
            actions_en_cours = [
                a for a in prep_context.previous_actions 
                if a.status != ActionStatus.COMPLETED
            ]
            if actions_en_cours:
                agenda["items"].insert(0, {
                    "order": 0,
                    "topic": f"Suivi des {len(actions_en_cours)} actions précédentes",
                    "duration_minutes": 5,
                    "notes": "Rappel des actions non complétées"
                })
        
        return agenda
    
    def suggest_critical_points(
        self,
        meeting_type: MeetingType,
        prep_context: MeetingPrepContext
    ) -> Dict[str, Any]:
        """
        Identifie les points critiques à traiter selon le contexte
        """
        critical = {
            "overdue_actions": [
                a for a in prep_context.previous_actions 
                if a.status == ActionStatus.OVERDUE
            ],
            "blocked_actions": [
                a for a in prep_context.previous_actions 
                if a.status == ActionStatus.BLOCKED
            ],
            "high_priority": [
                a for a in prep_context.previous_actions 
                if a.priority == ActionPriority.SHORT_TERM
                and a.status != ActionStatus.COMPLETED
            ]
        }
        
        return {
            "count": len(critical["overdue_actions"]) + len(critical["blocked_actions"]),
            "issues": critical,
            "recommendation": self._get_critical_recommendation(critical, meeting_type)
        }
    
    def _get_critical_recommendation(self, critical: Dict, meeting_type: MeetingType) -> str:
        """Recommandation basée sur les points critiques"""
        if not any(critical.values()):
            return "Pas de points critiques détectés."
        
        total = len(critical["overdue_actions"]) + len(critical["blocked_actions"])
        if total > 0:
            return f"⚠️ {total} actions critiques à résoudre. Recommandation: traiter ces blocages en début de réunion."
        
        return "Points d'attention normaux pour cette réunion."
    
    def suggest_facilitator(
        self,
        participants: List[Participant],
        meeting_type: MeetingType
    ) -> Participant:
        """
        Propose un animateur pour la réunion
        Priorité: responsable > séniorité > dernier animateur
        """
        # Préférer quelqu'un avec un rôle de responsable/manager
        managers = [p for p in participants if p.role and 'manager' in p.role.lower()]
        if managers:
            return managers[0]
        
        # Sinon, le premier participant
        return participants[0] if participants else None
    
    # ============ CAPTURE / ANALYSE ============
    
    def extract_meeting_data(
        self,
        meeting_id: str,
        meeting_type: MeetingType,
        source_content: str,  # Notes, transcription, etc.
        participants_email: List[str]
    ) -> Dict[str, Any]:
        """
        Extrait participants, décisions, actions d'un contenu brut
        Utilise ChromaDB pour contextualiser
        """
        # Chercher dans la base de connaissances pour contextualiser
        context_results = self.meetings_collection.query(
            query_texts=[source_content],
            n_results=3
        )
        
        context_docs = "\n".join(context_results.get("documents", [[]])[0] or [])
        
        extracted = {
            "meeting_id": meeting_id,
            "meeting_type": meeting_type.value,
            "extracted_at": datetime.now().isoformat(),
            "participants": participants_email,
            "context": context_docs[:500],  # Les 500 premiers caractères
            "source_content_preview": source_content[:1000]
        }
        
        return extracted
    
    # ============ GÉNÉRATION PV ============
    
    def generate_minutes(
        self,
        meeting_data: Dict[str, Any],
        decisions: List[Decision],
        actions: List[Action],
        participants: List[Participant],
        facilitator: str,
        title: str,
        topics: List[str] = None,
        duration: int = None,
        next_meeting_date: str = None
    ) -> MeetingMinutes:
        """
        Génère un procès-verbal structuré et professionnel
        """
        minutes = MeetingMinutes(
            meeting_id=meeting_data.get("meeting_id", f"meeting_{datetime.now().timestamp()}"),
            date=meeting_data.get("date", datetime.now().isoformat()),
            meeting_type=MeetingType(meeting_data.get("meeting_type", "ponctuelle")),
            title=title,
            facilitator=facilitator,
            participants=participants,
            topics_discussed=topics or [],
            decisions=decisions,
            action_plan=actions,
            duration_minutes=duration,
            next_meeting_date=next_meeting_date,
            created_at=datetime.now().isoformat()
        )
        
        return minutes
    
    def format_minutes_as_html(self, minutes: MeetingMinutes) -> str:
        """
        Formate les PV en HTML professionnel
        """
        html = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <style>
        body {{ font-family: Arial, sans-serif; margin: 20px; color: #333; }}
        .header {{ background-color: #2c3e50; color: white; padding: 20px; border-radius: 5px; }}
        .header h1 {{ margin: 0; }}
        .meta {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin: 20px 0; }}
        .meta-item {{ border-left: 4px solid #3498db; padding-left: 10px; }}
        .section {{ margin: 30px 0; }}
        .section h2 {{ color: #2c3e50; border-bottom: 2px solid #3498db; padding-bottom: 10px; }}
        table {{ width: 100%; border-collapse: collapse; margin: 10px 0; }}
        th, td {{ border: 1px solid #ddd; padding: 12px; text-align: left; }}
        th {{ background-color: #ecf0f1; font-weight: bold; }}
        .status {{ padding: 4px 8px; border-radius: 3px; }}
        .status.completed {{ background-color: #27ae60; color: white; }}
        .status.in_progress {{ background-color: #f39c12; color: white; }}
        .status.blocked {{ background-color: #e74c3c; color: white; }}
        .status.assigned {{ background-color: #3498db; color: white; }}
        .status.overdue {{ background-color: #c0392b; color: white; }}
        .priority {{ padding: 4px 8px; border-radius: 3px; }}
        .priority.court_terme {{ background-color: #c0392b; color: white; }}
        .priority.moyen_terme {{ background-color: #f39c12; color: white; }}
        .priority.long_terme {{ background-color: #3498db; color: white; }}
        .footer {{ margin-top: 40px; font-size: 12px; color: #7f8c8d; border-top: 1px solid #ecf0f1; padding-top: 10px; }}
    </style>
</head>
<body>
    <div class="header">
        <h1>📋 Procès-Verbal de Réunion</h1>
        <p><strong>{minutes.title}</strong></p>
    </div>
    
    <div class="meta">
        <div class="meta-item">
            <strong>Date:</strong> {minutes.date}<br>
            <strong>Type:</strong> {minutes.meeting_type.value.replace('_', ' ').title()}<br>
            <strong>Animateur:</strong> {minutes.facilitator}
        </div>
        <div class="meta-item">
            <strong>Participants ({len(minutes.participants)}):</strong><br>
            {', '.join([f"{p.name} ({p.email})" for p in minutes.participants])}<br>
            <strong>Durée:</strong> {minutes.duration_minutes or 'N/A'} minutes
        </div>
    </div>
    
    <div class="section">
        <h2>📌 Points Abordés</h2>
        <ul>
            {''.join([f"<li>{topic}</li>" for topic in minutes.topics_discussed])}
        </ul>
    </div>
    
    <div class="section">
        <h2>✅ Décisions Prises</h2>
        <table>
            <tr>
                <th>Décision</th>
                <th>Impact</th>
            </tr>
            {''.join([f"""
            <tr>
                <td>{d.description}</td>
                <td>{d.impact or '-'}</td>
            </tr>
            """ for d in minutes.decisions])}
        </table>
    </div>
    
    <div class="section">
        <h2>📋 Plan d'Action</h2>
        <table>
            <tr>
                <th>Action</th>
                <th>Responsable</th>
                <th>Échéance</th>
                <th>Priorité</th>
                <th>Statut</th>
                <th>KPI</th>
            </tr>
            {''.join([f"""
            <tr>
                <td>{a.description}</td>
                <td>{a.owner}</td>
                <td>{a.deadline}</td>
                <td><span class="priority {a.priority.value}">{a.priority.value.replace('_', ' ')}</span></td>
                <td><span class="status {a.status.value}">{a.status.value.replace('_', ' ')}</span></td>
                <td>{a.kpi or '-'}</td>
            </tr>
            """ for a in minutes.action_plan])}
        </table>
    </div>
    
    {'<p><strong>Prochaine réunion:</strong> ' + minutes.next_meeting_date + '</p>' if minutes.next_meeting_date else ''}
    
    <div class="footer">
        <p>Document généré automatiquement par l'Agent IA | {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}</p>
    </div>
</body>
</html>
        """
        return html
    
    def store_minutes(self, minutes: MeetingMinutes) -> str:
        """Stocke les PV dans ChromaDB"""
        try:
            doc_content = f"""
Réunion: {minutes.title}
Date: {minutes.date}
Type: {minutes.meeting_type.value}
Animateur: {minutes.facilitator}

POINTS ABORDÉS:
{chr(10).join(minutes.topics_discussed)}

DÉCISIONS:
{chr(10).join([f"- {d.description}" for d in minutes.decisions])}

PLAN D'ACTION:
{chr(10).join([f"- {a.description} (Responsable: {a.owner}, Échéance: {a.deadline})" for a in minutes.action_plan])}
            """
            
            self.meetings_collection.add(
                ids=[minutes.meeting_id],
                documents=[doc_content],
                metadatas=[{
                    "meeting_id": minutes.meeting_id,
                    "date": minutes.date,
                    "type": minutes.meeting_type.value,
                    "facilitator": minutes.facilitator,
                    "num_decisions": len(minutes.decisions),
                    "num_actions": len(minutes.action_plan)
                }]
            )
            
            self.logger.info(f"✅ PV stocké: {minutes.meeting_id}")
            return minutes.meeting_id
        except Exception as e:
            self.logger.error(f"Erreur stockage PV: {e}")
            return None
    
    # ============ SUIVI ============
    
    def get_action_followup(self, action_id: str) -> Dict[str, Any]:
        """Récupère le statut de suivi d'une action"""
        results = self.meetings_collection.query(
            query_texts=[action_id],
            n_results=1
        )
        
        if results.get("documents") and results["documents"][0]:
            return {
                "found": True,
                "data": results["documents"][0][0]
            }
        return {"found": False}
    
    def update_action_status(
        self,
        action_id: str,
        status: ActionStatus,
        notes: str = None
    ) -> bool:
        """Met à jour le statut d'une action"""
        # À implémenter avec une vraie BD pour les updates
        # Pour l'instant, logger
        self.logger.info(f"Action {action_id}: {status.value} - {notes}")
        return True
    
    def get_upcoming_deadlines(self, days_ahead: int = 7) -> List[Action]:
        """Récupère les actions avec deadline proche"""
        today = datetime.now()
        deadline_threshold = today + timedelta(days=days_ahead)
        
        # À implémenter avec requête sur la BD
        return []
    
    def get_overdue_actions(self) -> List[Action]:
        """Actions en retard à relancer"""
        return []
    
    def remind_participants(
        self,
        meeting_id: str,
        minutes: MeetingMinutes,
        email_service
    ) -> Dict[str, bool]:
        """
        Envoie les rappels et PV aux participants
        """
        results = {}
        
        # Grouper les actions par responsable
        actions_by_owner = {}
        for action in minutes.action_plan:
            if action.owner not in actions_by_owner:
                actions_by_owner[action.owner] = []
            actions_by_owner[action.owner].append(action)
        
        # Envoyer à chaque participant
        for participant in minutes.participants:
            email_body = f"""
Bonjour {participant.name},

Veuillez trouver ci-joint le procès-verbal de la réunion du {minutes.date}.

RÉSUMÉ:
Réunion: {minutes.title}
Animateur: {minutes.facilitator}

POINTS CLÉS:
{chr(10).join(minutes.topics_discussed[:5])}

PLAN D'ACTION:
"""
            # Actions assignées à ce participant
            if participant.email in actions_by_owner:
                email_body += f"\nVos actions ({len(actions_by_owner[participant.email])}):\n"
                for action in actions_by_owner[participant.email]:
                    email_body += f"- {action.description} (Échéance: {action.deadline})\n"
            
            email_body += "\nMerci."
            
            # Appeler le service d'email (à implémenter)
            # success = email_service.send(participant.email, f"PV - {minutes.title}", email_body)
            results[participant.email] = True  # Placeholder
            self.logger.info(f"📧 PV envoyé à {participant.email}")
        
        return results
