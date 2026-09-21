"""
Module Google Calendar
Service d'authentification + outils LangChain

Fonctionnalités :
- Authentification Google Calendar
- Lecture des événements
- Recherche d'événements
- Création d'événements
- Modification d'événements
- Suppression d'événements
- Gestion du timezone Africa/Casablanca
- Retour des IDs Google Calendar
"""

import os
import pickle
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from zoneinfo import ZoneInfo

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from langchain_core.tools import tool

from config import logger, CALENDAR_SCOPES, CALENDAR_TOKEN_FILE


# =========================================================
# CONFIGURATION
# =========================================================

MOROCCO_TZ = ZoneInfo("Africa/Casablanca")


# =========================================================
# UTILITAIRE : NORMALISATION DES DATES
# =========================================================

def normalize_calendar_datetime(value: str) -> str:
    """
    Normalise une date ISO 8601 pour Google Calendar.

    Exemples acceptés :

        2026-09-15T15:00:00
        2026-09-15T15:00:00+01:00
        2026-09-15T14:00:00Z

    Si aucune timezone n'est fournie :
        l'heure est considérée comme heure locale du Maroc.

    Retour :
        ISO 8601 avec timezone.
    """

    if not value:
        raise ValueError(
            "La date ne peut pas être vide."
        )

    value = str(value).strip()

    # -----------------------------------------------------
    # Support du format UTC avec Z
    # -----------------------------------------------------

    if value.endswith("Z"):
        value = value[:-1] + "+00:00"

    # -----------------------------------------------------
    # Conversion ISO
    # -----------------------------------------------------

    try:
        dt = datetime.fromisoformat(value)

    except ValueError as e:

        raise ValueError(
            f"Format de date invalide : {value}. "
            f"Utilisez par exemple : "
            f"2026-09-15T15:00:00"
        ) from e

    # -----------------------------------------------------
    # Pas de timezone
    # -----------------------------------------------------
    # On considère que l'heure donnée est l'heure
    # locale du Maroc.
    # -----------------------------------------------------

    if dt.tzinfo is None:
        dt = dt.replace(
            tzinfo=MOROCCO_TZ
        )

    # -----------------------------------------------------
    # Conversion vers le timezone marocain
    # -----------------------------------------------------

    dt = dt.astimezone(
        MOROCCO_TZ
    )

    return dt.isoformat()


# =========================================================
# SERVICE GOOGLE CALENDAR
# =========================================================

class GoogleCalendarService:
    """
    Service responsable de toutes les interactions
    avec Google Calendar.
    """

    def __init__(
        self,
        credentials_file: str = "calendar_credentials.json"
    ):
        self.credentials_file = credentials_file
        self.token_file = CALENDAR_TOKEN_FILE
        self.service = self._authenticate()

    # =====================================================
    # AUTHENTIFICATION
    # =====================================================

    def _authenticate(self):
        """
        Authentifie l'utilisateur auprès de Google Calendar.
        """

        creds = None

        # -------------------------------------------------
        # Charger le token existant
        # -------------------------------------------------

        if os.path.exists(self.token_file):

            with open(
                self.token_file,
                "rb"
            ) as token:

                creds = pickle.load(token)

        # -------------------------------------------------
        # Vérifier les credentials
        # -------------------------------------------------

        if not creds or not creds.valid:

            # ---------------------------------------------
            # Refresh du token
            # ---------------------------------------------

            if (
                creds
                and creds.expired
                and creds.refresh_token
            ):

                logger.info(
                    "Refreshing Google Calendar credentials..."
                )

                creds.refresh(
                    Request()
                )

            # ---------------------------------------------
            # Nouvelle authentification
            # ---------------------------------------------

            else:

                if not os.path.exists(
                    self.credentials_file
                ):

                    raise FileNotFoundError(
                        f"{self.credentials_file} not found. "
                        "Please download it from Google Cloud Console."
                    )

                flow = (
                    InstalledAppFlow
                    .from_client_secrets_file(
                        self.credentials_file,
                        CALENDAR_SCOPES
                    )
                )

                creds = flow.run_local_server(
                    port=0
                )

            # ---------------------------------------------
            # Sauvegarder le token
            # ---------------------------------------------

            with open(
                self.token_file,
                "wb"
            ) as token:

                pickle.dump(
                    creds,
                    token
                )

        return build(
            "calendar",
            "v3",
            credentials=creds
        )

    # =====================================================
    # LECTURE
    # =====================================================

    def get_events(
        self,
        time_min: Optional[str] = None,
        time_max: Optional[str] = None,
        max_results: int = 50
    ) -> List[Dict]:
        """
        Récupère les événements dans une plage de temps.
        """

        if time_min is None:

            time_min = datetime.now(
                MOROCCO_TZ
            ).isoformat()

        events = (
            self.service.events().list(
                calendarId="primary",
                timeMin=time_min,
                timeMax=time_max,
                singleEvents=True,
                orderBy="startTime",
                maxResults=max_results,
            )
            .execute()
        )

        return events.get(
            "items",
            []
        )

    # =====================================================
    # AUJOURD'HUI
    # =====================================================

    def get_today_events(self):
        """
        Récupère tous les événements d'aujourd'hui
        selon le timezone du Maroc.
        """

        now = datetime.now(
            MOROCCO_TZ
        )

        start = datetime(
            now.year,
            now.month,
            now.day,
            0,
            0,
            0,
            tzinfo=MOROCCO_TZ
        )

        end = start + timedelta(
            days=1
        )

        return self.get_events(
            start.isoformat(),
            end.isoformat()
        )

    # =====================================================
    # SEMAINE
    # =====================================================

    def get_week_events(self):
        """
        Récupère les événements des
        7 prochains jours.
        """

        now = datetime.now(
            MOROCCO_TZ
        )

        end = now + timedelta(
            days=7
        )

        return self.get_events(
            now.isoformat(),
            end.isoformat()
        )

    # =====================================================
    # PROCHAIN ÉVÉNEMENT
    # =====================================================

    def get_next_event(self):

        events = self.get_events(
            max_results=1
        )

        if not events:
            return None

        return events[0]

    # =====================================================
    # COMPTER LES ÉVÉNEMENTS
    # =====================================================

    def count_events_this_week(self):

        return len(
            self.get_week_events()
        )

    # =====================================================
    # RECHERCHE
    # =====================================================

    def search_events(
        self,
        text: str
    ):
        """
        Recherche des événements par texte.
        """

        result = (
            self.service
            .events()
            .list(
                calendarId="primary",
                q=text,
                singleEvents=True,
                orderBy="startTime",
            )
            .execute()
        )

        return result.get(
            "items",
            []
        )

    # =====================================================
    # CRÉATION
    # =====================================================

    def create_event(
        self,
        summary: str,
        start: str,
        end: str,
        description: str = "",
        location: str = "",
        attendees: Optional[List[str]] = None,
    ):
        """
        Crée un nouvel événement.

        Les dates sans timezone sont considérées
        comme des heures locales du Maroc.
        """

        # -------------------------------------------------
        # Normalisation
        # -------------------------------------------------

        start_normalized = (
            normalize_calendar_datetime(
                start
            )
        )

        end_normalized = (
            normalize_calendar_datetime(
                end
            )
        )

        logger.info(
            f"📅 Création événement : {summary}"
        )

        logger.info(
            f"🕐 Start reçu : {start}"
        )

        logger.info(
            f"🕐 Start normalisé : "
            f"{start_normalized}"
        )

        logger.info(
            f"🕐 End reçu : {end}"
        )

        logger.info(
            f"🕐 End normalisé : "
            f"{end_normalized}"
        )

        # -------------------------------------------------
        # Vérifier la cohérence
        # -------------------------------------------------

        start_dt = datetime.fromisoformat(
            start_normalized
        )

        end_dt = datetime.fromisoformat(
            end_normalized
        )

        if end_dt <= start_dt:

            raise ValueError(
                "La date de fin doit être "
                "postérieure à la date de début."
            )

        # -------------------------------------------------
        # Construire l'événement
        # -------------------------------------------------

        event = {

            "summary": summary,

            "description": description,

            "location": location,

            "start": {
                "dateTime": start_normalized,
                "timeZone": "Africa/Casablanca",
            },

            "end": {
                "dateTime": end_normalized,
                "timeZone": "Africa/Casablanca",
            },

            "attendees": [
                {
                    "email": email
                }
                for email in (
                    attendees or []
                )
            ],

            "conferenceData": {

                "createRequest": {

                    "requestId": (
                        f"{summary}-"
                        f"{datetime.now().timestamp()}"
                    )

                }

            }

        }

        # -------------------------------------------------
        # Envoyer à Google Calendar
        # -------------------------------------------------

        created = (
            self.service
            .events()
            .insert(
                calendarId="primary",
                body=event,
                conferenceDataVersion=1,
            )
            .execute()
        )

        # -------------------------------------------------
        # Logs
        # -------------------------------------------------

        logger.info(
            "✅ Événement créé"
        )

        logger.info(
            f"🆔 Event ID : "
            f"{created.get('id')}"
        )

        logger.info(
            f"📅 Start Google : "
            f"{created.get('start', {}).get('dateTime')}"
        )

        logger.info(
            f"📅 End Google : "
            f"{created.get('end', {}).get('dateTime')}"
        )

        return created

    # =====================================================
    # MODIFICATION
    # =====================================================

    def update_event(
        self,
        event_id: str,
        **kwargs
    ):
        """
        Modifie un événement existant.
        """

        event = (
            self.service
            .events()
            .get(
                calendarId="primary",
                eventId=event_id
            )
            .execute()
        )

        for key, value in kwargs.items():

            if value is not None:

                event[key] = value

        updated = (
            self.service
            .events()
            .update(
                calendarId="primary",
                eventId=event_id,
                body=event
            )
            .execute()
        )

        logger.info(
            f"✅ Événement mis à jour : "
            f"{updated.get('id')}"
        )

        return updated

    # =====================================================
    # SUPPRESSION
    # =====================================================

    def delete_event(
        self,
        event_id: str
    ):
        """
        Supprime un événement.
        """

        self.service.events().delete(
            calendarId="primary",
            eventId=event_id
        ).execute()

        logger.info(
            f"🗑️ Événement supprimé : "
            f"{event_id}"
        )

        return True

    # =====================================================
    # RÉCUPÉRER PAR ID
    # =====================================================

    def get_event_by_id(
        self,
        event_id: str
    ):

        return (
            self.service
            .events()
            .get(
                calendarId="primary",
                eventId=event_id
            )
            .execute()
        )


# =========================================================
# INITIALISATION
# =========================================================

calendar_service = GoogleCalendarService()

logger.info(
    "✅ Google Calendar service initialized"
)


# =========================================================
# TOOL : AUJOURD'HUI
# =========================================================

@tool
def get_today_events() -> str:
    """
    Retourne toutes les réunions prévues aujourd'hui.

    Utiliser lorsque l'utilisateur demande :

    - Qu'ai-je aujourd'hui ?
    - Mes réunions du jour.
    - Quels sont mes rendez-vous aujourd'hui ?
    """

    events = (
        calendar_service
        .get_today_events()
    )

    if not events:

        return (
            "Vous n'avez aucune "
            "réunion aujourd'hui."
        )

    response = (
        f"Vous avez {len(events)} "
        f"réunion(s) aujourd'hui :\n\n"
    )

    for event in events:

        event_id = event.get(
            "id",
            "ID indisponible"
        )

        start = event["start"].get(
            "dateTime",
            event["start"].get("date")
        )

        response += (
            f"• {event.get('summary', 'Sans titre')}\n"
            f"  ID : {event_id}\n"
            f"  Début : {start}\n\n"
        )

    return response


# =========================================================
# TOOL : SEMAINE
# =========================================================

@tool
def get_week_events() -> str:
    """
    Retourne toutes les réunions
    des 7 prochains jours.
    """

    events = (
        calendar_service
        .get_week_events()
    )

    if not events:

        return (
            "Aucune réunion "
            "cette semaine."
        )

    response = (
        f"Vous avez {len(events)} "
        f"réunion(s) cette semaine.\n\n"
    )

    for event in events:

        event_id = event.get(
            "id",
            "ID indisponible"
        )

        start = event["start"].get(
            "dateTime",
            event["start"].get("date")
        )

        response += (
            f"• {event.get('summary', 'Sans titre')}\n"
            f"  ID : {event_id}\n"
            f"  Début : {start}\n\n"
        )

    return response


# =========================================================
# TOOL : COMPTER
# =========================================================

@tool
def count_week_events() -> str:
    """
    Retourne le nombre de réunions
    des 7 prochains jours.
    """

    count = (
        calendar_service
        .count_events_this_week()
    )

    return (
        f"Vous avez {count} "
        f"réunion(s) cette semaine."
    )


# =========================================================
# TOOL : PROCHAINE RÉUNION
# =========================================================

@tool
def get_next_meeting() -> str:
    """
    Retourne la prochaine réunion.
    """

    event = (
        calendar_service
        .get_next_event()
    )

    if event is None:

        return (
            "Vous n'avez aucune "
            "réunion programmée."
        )

    event_id = event.get(
        "id",
        "ID indisponible"
    )

    start = event["start"].get(
        "dateTime",
        event["start"].get("date")
    )

    return (
        "Prochaine réunion :\n\n"
        f"ID : {event_id}\n"
        f"Titre : "
        f"{event.get('summary', 'Sans titre')}\n"
        f"Début : {start}"
    )


# =========================================================
# TOOL : RECHERCHE
# =========================================================

@tool
def search_calendar(
    search_text: str
) -> str:
    """
    Recherche une réunion dans Google Calendar.

    Exemple :

    - Recherche Budget
    - Recherche Ahmed
    - Recherche présentation
    """

    events = (
        calendar_service
        .search_events(
            search_text
        )
    )

    if not events:

        return (
            "Aucun événement trouvé."
        )

    response = (
        f"{len(events)} "
        f"événement(s) trouvé(s).\n\n"
    )

    for event in events:

        event_id = event.get(
            "id",
            "ID indisponible"
        )

        start = event["start"].get(
            "dateTime",
            event["start"].get("date")
        )

        response += (
            f"• {event.get('summary', 'Sans titre')}\n"
            f"  ID : {event_id}\n"
            f"  Début : {start}\n\n"
        )

    return response


# =========================================================
# TOOL : CRÉER
# =========================================================

@tool
def create_calendar_event(
    summary: str,
    start: str,
    end: str,
    description: str = "",
    location: str = ""
) -> str:
    """
    Crée une réunion dans Google Calendar.

    Les dates doivent être au format ISO8601.

    Si aucune timezone n'est fournie,
    l'heure est considérée comme heure
    locale du Maroc.

    Exemple :

    start="2026-09-15T14:00:00"
    end="2026-09-15T15:00:00"
    """

    event = (
        calendar_service
        .create_event(
            summary=summary,
            start=start,
            end=end,
            description=description,
            location=location,
        )
    )

    event_id = event.get(
        "id",
        "ID indisponible"
    )

    start_created = (
        event
        .get("start", {})
        .get(
            "dateTime",
            start
        )
    )

    end_created = (
        event
        .get("end", {})
        .get(
            "dateTime",
            end
        )
    )

    return (
        "Réunion créée avec succès.\n\n"
        f"ID : {event_id}\n"
        f"Titre : "
        f"{event.get('summary', summary)}\n"
        f"Début : {start_created}\n"
        f"Fin : {end_created}"
    )


# =========================================================
# TOOL : MODIFIER
# =========================================================

@tool
def update_calendar_event(
    event_id: str,
    summary: Optional[str] = None,
    location: Optional[str] = None,
    description: Optional[str] = None,
) -> str:
    """
    Modifie une réunion existante.

    Utiliser l'ID Google Calendar
    de l'événement.
    """

    updates = {}

    if summary:
        updates["summary"] = summary

    if location:
        updates["location"] = location

    if description:
        updates["description"] = description

    event = (
        calendar_service
        .update_event(
            event_id,
            **updates
        )
    )

    return (
        "La réunion a été mise à jour.\n\n"
        f"ID : {event.get('id', event_id)}\n"
        f"Titre : "
        f"{event.get('summary', 'Sans titre')}"
    )


# =========================================================
# TOOL : SUPPRIMER
# =========================================================

@tool
def delete_calendar_event(
    event_id: str
) -> str:
    """
    Supprime une réunion.

    Utiliser uniquement lorsque
    l'utilisateur demande explicitement
    de supprimer un événement.

    Utiliser l'ID Google Calendar
    de l'événement.
    """

    calendar_service.delete_event(
        event_id
    )

    return (
        "La réunion a été supprimée.\n\n"
        f"ID : {event_id}"
    )


# =========================================================
# EXPORT DES TOOLS
# =========================================================

calendar_tools = [

    get_today_events,

    get_week_events,

    count_week_events,

    get_next_meeting,

    search_calendar,

    create_calendar_event,

    update_calendar_event,

    delete_calendar_event,

]
