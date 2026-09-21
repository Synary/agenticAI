"""
Module de transcription audio
Support pour Deepgram et AssemblyAI
"""
import logging
from typing import Optional
from assemblyai.prerecorded.v2 import Transcriber

from config import logger, transcriber
def transcribe_audio_file(file_content: bytes, file_name: str) -> str:
    """
    Transcrit un fichier audio en utilisant AssemblyAI.

    Args:
        file_content: Contenu binaire du fichier audio
        file_name: Nom du fichier audio

    Returns:
        Texte transcrit du fichier audio
    """
    logger.info(f"🎙️ Transcribing audio file: {file_name}")

    try:
        import tempfile
        with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp_file:
            tmp_file.write(file_content)
            tmp_path = tmp_file.name

        # Transcrire avec AssemblyAI
        transcript = transcriber.transcribe(tmp_path)

        # Nettoyage
        import os
        os.unlink(tmp_path)

        if transcript and hasattr(transcript, 'text'):
            text = transcript.text
            logger.info(f"✅ Transcription complete for {file_name}: {len(text)} characters")
            return text
        else:
            logger.warning(f"⚠️ No text from transcription for {file_name}")
            return f"[Transcription incomplete for {file_name}]"

    except Exception as e:
        logger.error(f"❌ Error transcribing {file_name}: {str(e)}")
        return f"[Error transcribing {file_name}: {str(e)}]"


