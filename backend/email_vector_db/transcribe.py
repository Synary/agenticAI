from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from deepgram import DeepgramClient
import os

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
DEEPGRAM_API_KEY = "863c71021fa0421e89b5ab36b15e90a1ed8c4d47"

deepgram = DeepgramClient(api_key=DEEPGRAM_API_KEY)


@app.post("/transcribe")
async def transcribe(file: UploadFile = File(...)):

    try:
        audio = await file.read()

        response = deepgram.listen.v1.media.transcribe_file(
            {"buffer": audio},
            {
                "model": "nova-3",
                "language": "fr",
                "smart_format": True,
                "punctuate": True,
                "diarize": True,
            }
        )

        transcript = (
            response.results
            .channels[0]
            .alternatives[0]
            .transcript
        )

        return {
            "filename": file.filename,
            "text": transcript
        }

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=str(e)
        )