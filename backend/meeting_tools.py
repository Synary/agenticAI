"""
Outils LangGraph pour gérer les réunions
À intégrer dans main.py
"""
import json
import logging
from typing import Optional, List
from langchain_core.tools import tool
from datetime import datetime, timedelta

from meetings import (
    MeetingManager, MeetingType, ActionPriority, ActionStatus,
    Action, Decision, Participant, MeetingMinutes
)

logger = logging.getLogger(__name__)

# Note: Ces outils supposent que MeetingManager est initialisé
# Voir intégration à la fin du fichier

# ============ OUTILS DE PRÉPARATION ============

@tool
def prepare_meeting(
    meeting_type: str,
    meeting_date: str,
    meeting_title: str = None
) -> str:
    """
    Prépare une réunion: propose agenda, contexte, points critiques.
    
    Args:
        meeting_type: Type de réunion (commercial, marketing, financier, strategique, hebdomadaire, bihebdomadaire, ponctuelle)
        meeting_date: Date ISO de la réunion
        meeting_title: Titre optionnel de la réunion
    
    Returns:
        Résumé de la préparation avec agenda et points critiques
    """
    try:
        from config import meeting_manager  # Import runtime
        
        meeting_type_enum = MeetingType(meeting_type.lower())
        
        # Récupérer le contexte
        prep_context = meeting_manager.get_meeting_prep_context(
            meeting_type_enum,
            meeting_date
        )
        
        # Proposer l'agenda
        agenda = meeting_manager.suggest_agenda(
            meeting_type_enum,
            prep_context,
            duration_minutes=60
        )
        
        # Points critiques
        critical = meeting_manager.suggest_critical_points(
            meeting_type_enum,
            prep_context
        )
        
        response = f"""
📋 PRÉPARATION DE RÉUNION: {meeting_title or meeting_type_enum.value}

📅 Date: {meeting_date}
🎯 Type: {meeting_type_enum.value.replace('_', ' ').title()}
🆔 ID: {prep_context.meeting_id}

---
CONTEXTE DE LA RÉUNION PRÉCÉDENTE:
- Actions assignées: {len(prep_context.previous_actions)}
- Décisions prises: {len(prep_context.previous_decisions)}
- Statuts: {json.dumps(prep_context.action_status_summary, ensure_ascii=False)}

---
⚠️ POINTS CRITIQUES:
{critical.get('recommendation', 'Aucun point critique')}

---
📌 ORDRE DU JOUR PROPOSÉ (durée totale: {agenda['duration_total']} min):
"""
        
        for item in agenda['items']:
            response += f"\n{item['order']}. {item['topic']} ({item['duration_minutes']} min)"
        
        return response
        
    except Exception as e:
        logger.error(f"Erreur préparation réunion: {e}")
        return f"Erreur lors de la préparation: {str(e)}"


@tool
def get_meeting_facilitator_suggestion(participants_emails: str) -> str:
    """
    Suggère un animateur pour la réunion basé sur les participants.
    
    Args:
        participants_emails: Liste d'emails séparés par des virgules
    
    Returns:
        Suggestion d'animateur
    """
    try:
        from config import meeting_manager
        
        emails = [e.strip() for e in participants_emails.split(",")]
        # Créer des objets Participant simples (à améliorer avec vrai profil)
        participants = [
            Participant(email=e, name=e.split("@")[0])
            for e in emails
        ]
        
        facilitator = meeting_manager.suggest_facilitator(
            participants,
            MeetingType.WEEKLY
        )
        
        if facilitator:
            return f"Animateur suggéré: {facilitator.name} ({facilitator.email})"
        else:
            return "Impossible de suggérer un animateur (pas de participants)"
        
    except Exception as e:
        logger.error(f"Erreur suggestion animateur: {e}")
        return f"Erreur: {str(e)}"


# ============ OUTILS DE CAPTURE ============

@tool
def analyze_meeting_transcript(
    transcript: str,
    meeting_type: str,
    participants_emails: str,
    meeting_date: str = None
) -> str:
    """
    Analyse une transcription/notes de réunion pour extraire:
    - Participants présents
    - Points abordés
    - Décisions
    - Actions
    
    Args:
        transcript: Contenu de la transcription ou notes brutes
        meeting_type: Type de réunion
        participants_emails: Emails des participants
        meeting_date: Date ISO de la réunion (par défaut: maintenant)
    
    Returns:
        Résumé des données extraites
    """
    try:
        from config import meeting_manager
        
        meeting_type_enum = MeetingType(meeting_type.lower())
        emails = [e.strip() for e in participants_emails.split(",")]
        meeting_date = meeting_date or datetime.now().isoformat()
        
        # Extraire les données
        extracted = meeting_manager.extract_meeting_data(
            meeting_id=f"meeting_{datetime.now().timestamp()}",
            meeting_type=meeting_type_enum,
            source_content=transcript,
            participants_email=emails
        )
        
        response = f"""
✅ ANALYSE DE LA TRANSCRIPTION

📅 Date: {meeting_date}
🎯 Type: {meeting_type_enum.value}
👥 Participants: {', '.join(emails)}

APERÇU DU CONTENU:
{extracted.get('source_content_preview', '')[:500]}

CONTEXTE RÉCUPÉRÉ:
{extracted.get('context', '')[:300]}

ID de la réunion: {extracted['meeting_id']}

⚠️ PROCHAINE ÉTAPE: Fournir les décisions et actions spécifiques pour générer le PV complet.
        """
        return response
        
    except Exception as e:
        logger.error(f"Erreur analyse transcription: {e}")
        return f"Erreur: {str(e)}"


# ============ OUTILS DE GÉNÉRATION PV ============

@tool
def generate_meeting_minutes(
    meeting_id: str,
    title: str,
    meeting_type: str,
    facilitator: str,
    participants_emails: str,
    topics_list: str,
    decisions_json: str = "[]",
    actions_json: str = "[]",
    meeting_date: str = None,
    duration_minutes: int = 60,
    next_meeting_date: str = None
) -> str:
    """
    Génère un procès-verbal complet et structuré.
    
    Args:
        meeting_id: ID unique de la réunion
        title: Titre de la réunion
        meeting_type: Type (commercial, marketing, financier, strategique, hebdomadaire, bihebdomadaire, ponctuelle)
        facilitator: Nom de l'animateur
        participants_emails: Emails séparés par des virgules
        topics_list: Sujets abordés (un par ligne, ou JSON list)
        decisions_json: JSON array de décisions [{"description": "...", "impact": "..."}]
        actions_json: JSON array d'actions [{"description": "...", "owner": "...", "deadline": "...", "priority": "..."}]
        meeting_date: Date ISO (par défaut: maintenant)
        duration_minutes: Durée en minutes
        next_meeting_date: Date de la prochaine réunion
    
    Returns:
        HTML du PV généré + stocké
    """
    try:
        from config import meeting_manager
        
        meeting_type_enum = MeetingType(meeting_type.lower())
        meeting_date = meeting_date or datetime.now().isoformat()
        
        # Parser les participants
        participant_emails = [e.strip() for e in participants_emails.split(",")]
        participants = [
            Participant(email=e, name=e.split("@")[0])
            for e in participant_emails
        ]
        
        # Parser les topics
        try:
            topics = json.loads(topics_list) if topics_list.startswith("[") else topics_list.split("\n")
        except:
            topics = [t.strip() for t in topics_list.split("\n") if t.strip()]
        
        # Parser les décisions
        try:
            decisions_data = json.loads(decisions_json) if decisions_json != "[]" else []
            decisions = [
                Decision(
                    id=f"dec_{i}",
                    description=d.get("description", ""),
                    impact=d.get("impact"),
                    requires_action=d.get("requires_action", False),
                    meeting_id=meeting_id
                )
                for i, d in enumerate(decisions_data)
            ]
        except:
            decisions = []
        
        # Parser les actions
        try:
            actions_data = json.loads(actions_json) if actions_json != "[]" else []
            actions = [
                Action(
                    id=f"act_{i}",
                    description=a.get("description", ""),
                    owner=a.get("owner", "À désigner"),
                    deadline=a.get("deadline", (datetime.now() + timedelta(days=7)).isoformat()),
                    priority=ActionPriority(a.get("priority", "moyen_terme")),
                    status=ActionStatus(a.get("status", "assignée")),
                    kpi=a.get("kpi"),
                    meeting_id=meeting_id
                )
                for i, a in enumerate(actions_data)
            ]
        except Exception as e:
            logger.error(f"Erreur parsing actions: {e}")
            actions = []
        
        # Générer les minutes
        minutes = meeting_manager.generate_minutes(
            meeting_data={
                "meeting_id": meeting_id,
                "date": meeting_date,
                "meeting_type": meeting_type
            },
            decisions=decisions,
            actions=actions,
            participants=participants,
            facilitator=facilitator,
            title=title,
            topics=topics,
            duration=duration_minutes,
            next_meeting_date=next_meeting_date
        )
        
        # Stocker dans ChromaDB
        stored_id = meeting_manager.store_minutes(minutes)
        
        # Générer HTML
        html = meeting_manager.format_minutes_as_html(minutes)
        
        response = f"""
✅ PROCÈS-VERBAL GÉNÉRÉ ET STOCKÉ

📋 Titre: {title}
🎯 Type: {meeting_type_enum.value}
📅 Date: {meeting_date}
🎤 Animateur: {facilitator}

RÉSUMÉ:
- Points abordés: {len(topics)}
- Décisions: {len(decisions)}
- Actions: {len(actions)}
- Participants: {len(participants)}

📌 Stocké sous ID: {stored_id}

PROCHAINES ÉTAPES:
1. Envoyer le PV par email aux participants
2. Programmer les rappels (2-3 jours avant prochaine réunion)
3. Tracker les actions

Le PV est prêt à être envoyé en HTML.
        """
        return response
        
    except Exception as e:
        logger.error(f"Erreur génération PV: {e}")
        return f"Erreur: {str(e)}"


# ============ OUTILS DE SUIVI ============

@tool
def check_action_status(action_id: str) -> str:
    """Récupère le statut d'une action"""
    try:
        from config import meeting_manager
        
        result = meeting_manager.get_action_followup(action_id)
        
        if result["found"]:
            return f"✅ Action trouvée:\n{result['data']}"
        else:
            return f"❌ Action {action_id} non trouvée"
        
    except Exception as e:
        logger.error(f"Erreur check action: {e}")
        return f"Erreur: {str(e)}"


@tool
def update_action(
    action_id: str,
    new_status: str,
    notes: str = None
) -> str:
    """
    Met à jour le statut d'une action.
    
    Args:
        action_id: ID de l'action
        new_status: Nouveau statut (assignée, en_cours, bloquée, complétée, en_retard)
        notes: Notes optionnelles
    
    Returns:
        Confirmation de mise à jour
    """
    try:
        from config import meeting_manager
        
        status_enum = ActionStatus(new_status.lower())
        meeting_manager.update_action_status(action_id, status_enum, notes)
        
        return f"""
✅ ACTION MISE À JOUR

ID: {action_id}
Nouveau statut: {status_enum.value}
Notes: {notes or '-'}
Mis à jour: {datetime.now().isoformat()}
        """
        
    except Exception as e:
        logger.error(f"Erreur update action: {e}")
        return f"Erreur: {str(e)}"


@tool
def list_upcoming_deadlines(days_ahead: int = 7) -> str:
    """
    Liste les actions avec deadline dans les N prochains jours.
    
    Args:
        days_ahead: Nombre de jours à vérifier (défaut: 7)
    
    Returns:
        Liste des actions urgentes
    """
    try:
        from config import meeting_manager
        
        actions = meeting_manager.get_upcoming_deadlines(days_ahead)
        
        if not actions:
            return f"✅ Aucune deadline dans les {days_ahead} prochains jours"
        
        response = f"⏰ {len(actions)} actions avec deadline proche:\n"
        for action in actions:
            response += f"\n- [{action.priority.value}] {action.description}"
            response += f"\n  Responsable: {action.owner}"
            response += f"\n  Échéance: {action.deadline}\n"
        
        return response
        
    except Exception as e:
        logger.error(f"Erreur list deadlines: {e}")
        return f"Erreur: {str(e)}"


@tool
def list_overdue_actions() -> str:
    """Liste les actions en retard"""
    try:
        from config import meeting_manager
        
        actions = meeting_manager.get_overdue_actions()
        
        if not actions:
            return "✅ Aucune action en retard"
        
        response = f"⚠️ {len(actions)} actions EN RETARD:\n"
        for action in actions:
            response += f"\n- {action.description}"
            response += f"\n  Responsable: {action.owner}"
            response += f"\n  Deadline: {action.deadline}\n"
        
        return response
        
    except Exception as e:
        logger.error(f"Erreur list overdue: {e}")
        return f"Erreur: {str(e)}"

meeting_tools_list= [
    prepare_meeting,
    get_meeting_facilitator_suggestion,
    analyze_meeting_transcript,
    generate_meeting_minutes,
    check_action_status,
    update_action,
    list_upcoming_deadlines,
    list_overdue_actions
]
# ============ INTÉGRATION À CONFIG.PY ============

