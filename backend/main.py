from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
import os
import json
import requests
import speech_recognition as sr
from dotenv import load_dotenv
import asyncio
import tempfile
from pydantic import BaseModel
from typing import List, Optional

# Load environment variables from .env file
load_dotenv()

# Initialize FastAPI app
app = FastAPI(title="English Conversation Chatbot API", version="1.0.0")

# Allow CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Define Pydantic models
class ChatMessage(BaseModel):
    role: str
    content: str

class TextChatRequest(BaseModel):
    message: str
    conversation_history: List[ChatMessage] = []

class TextChatResponse(BaseModel):
    response: str
    conversation_history: List[ChatMessage]

class AudioTranscriptionResponse(BaseModel):
    text: str
    success: bool
    error: Optional[str] = None

# Define chatbot logic
class EnglishChatBot:
    def __init__(self):
        self.mistral_api_key = os.getenv("MISTRAL_API_KEY") or "your_actual_api_key_here"
        if not self.mistral_api_key:
            raise ValueError("MISTRAL_API_KEY is missing. Please set it in your environment variables.")
        self.recognizer = sr.Recognizer()
        self.system_prompt = (
            """
            You are an English conversation tutor. Analyze the user's sentence.

            If the sentence is correct:
            - Say: "✅ Looks good!"
            - Suggest ONE natural alternative.
            - Ask a simple follow-up question.

            If the sentence has mistakes:
            - Show:
              ✅ Corrected Sentence: [Correction]
              ❌ Mistake(s): List the grammar mistakes (e.g., wrong tense, missing article)
              💡 Alternatives:
                - [Alternative 1]
                - [Alternative 2]
            - Ask a follow-up question.

            Format:
            ✅ Corrected Sentence: ...
            ❌ Mistake(s): ...
            💡 Alternatives:
            - ...
            - ...
            ❓ Follow-up question
            """
        )

    async def generate_response(self, user_input: str, conversation_history: List[dict]) -> str:
        messages = [{"role": "system", "content": self.system_prompt}]
        messages.extend(conversation_history[-20:])
        messages.append({"role": "user", "content": user_input})

        headers = {
            "Authorization": f"Bearer {self.mistral_api_key}",
            "Content-Type": "application/json",
        }
        data = {
            "model": "mistral-small",  # you can change to mistral-medium or mistral-large-latest
            "messages": messages,
            "temperature": 0.7,
            "max_tokens": 500
        }

        try:
            response = requests.post("https://api.mistral.ai/v1/chat/completions", headers=headers, json=data, timeout=30)
            print(f"API Response Status: {response.status_code}")
            response.raise_for_status()
            return response.json()["choices"][0]["message"]["content"]
        except Exception as e:
            return f"Error generating response: {str(e)}"


    async def process_audio_file(self, audio_file: UploadFile) -> dict:
        try:
            audio_data = await audio_file.read()
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_file:
                tmp_file.write(audio_data)
                tmp_file_path = tmp_file.name

            with sr.AudioFile(tmp_file_path) as source:
                self.recognizer.adjust_for_ambient_noise(source, duration=0.2)
                audio = self.recognizer.record(source)
                text = self.recognizer.recognize_google(audio)

            os.unlink(tmp_file_path)
            return {"text": text, "success": True, "error": None}
        except sr.UnknownValueError:
            return {"text": "", "success": False, "error": "Could not understand audio."}
        except Exception as e:
            return {"text": "", "success": False, "error": str(e)}

# Initialize chatbot
try:
    chatbot = EnglishChatBot()
except Exception as e:
    print(f"Chatbot initialization failed: {e}")
    chatbot = None

@app.get("/")
async def health_check():
    return {"message": "English Chatbot API is running"}

@app.post("/chat/audio")
async def chat_with_audio(audio: UploadFile = File(...), conversation_history: str = "[]"):
    if not chatbot:
        raise HTTPException(status_code=503, detail="Chatbot not properly configured")

    try:
        history_list = json.loads(conversation_history)
        history = [ChatMessage(**msg) for msg in history_list]
        transcription = await chatbot.process_audio_file(audio)
        if not transcription["success"]:
            return {
                "transcription": "",
                "response": "",
                "conversation_history": history_list,
                "error": transcription["error"]
            }

        user_input = transcription["text"]
        ai_response = await chatbot.generate_response(user_input, [msg.model_dump() for msg in history])

        updated_history = history + [
            ChatMessage(role="user", content=user_input),
            ChatMessage(role="assistant", content=ai_response)
        ]

        return {
            "transcription": user_input,
            "response": ai_response,
            "conversation_history": [msg.model_dump() for msg in updated_history],
            "error": None
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.websocket("/ws/chat")
async def chat_websocket(websocket: WebSocket):
    await websocket.accept()
    if not chatbot:
        await websocket.send_json({"type": "error", "message": "Chatbot not configured"})
        await websocket.close()
        return

    history = []
    try:
        while True:
            data = await websocket.receive_text()
            msg = json.loads(data)

            if msg["type"] == "text_message":
                user_msg = msg["content"]
                response = await chatbot.generate_response(user_msg, history)
                history.append({"role": "user", "content": user_msg})
                history.append({"role": "assistant", "content": response})

                await websocket.send_json({
                    "type": "chat_response",
                    "user_message": user_msg,
                    "ai_response": response,
                    "conversation_history": history
                })
            elif msg["type"] == "clear_history":
                history.clear()
                await websocket.send_json({"type": "history_cleared", "message": "History cleared"})
    except WebSocketDisconnect:
        print("WebSocket disconnected")
    except Exception as e:
        await websocket.send_json({"type": "error", "message": str(e)})
        await websocket.close()
