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
import io
from pydantic import BaseModel
from typing import List, Optional
import base64

# It's better practice to call load_dotenv() at the top level
load_dotenv()

app = FastAPI(title="English Conversation Chatbot API", version="1.0.0")


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Pydantic models
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

class EnglishChatBot:
    def __init__(self):
        self.mistral_api_key = "a4Cuk8IlEzJHgJJoRMaZgj33FLTKTZTv"
        if not self.mistral_api_key:
            raise ValueError("MISTRAL_API_KEY not found in environment variables")
        
        self.recognizer = sr.Recognizer()
        
        self.system_prompt = """
You are an English conversation tutor. When the user speaks (converted to text), analyze their sentence.

If the sentence is correct:
- Say: "✅ Looks good!" and suggest 1 natural alternative.
- Ask a simple follow-up question to continue the conversation.

If the sentence has mistakes:
- Show:
  ✅ Corrected Sentence: [Correction]
  ❌ Mistake: [Brief description of mistakes]
  💡 Alternatives:
    - [Alternative 1]
    - [Alternative 2]
- Then ask a follow-up question to keep the conversation going.

Keep responses short, friendly, and conversational.

Example:
User: "He go school yesterday."
AI:
✅ Corrected Sentence: "He went to school yesterday."
❌ Mistake: Used 'go' instead of 'went'
💡 Alternatives:
- "Yesterday, he went to school."
- "He attended school yesterday."
❓ What did you do yesterday?
"""


    async def process_audio_file(self, audio_file: UploadFile) -> dict:
        """Convert uploaded audio file to text using speech recognition"""
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
            return {"text": "", "success": False, "error": "Could not understand the audio. Please speak more clearly."}
        except sr.RequestError as e:
            return {"text": "", "success": False, "error": f"Speech recognition service error: {str(e)}"}
        except Exception as e:
            return {"text": "", "success": False, "error": f"Error processing audio: {str(e)}"}

    async def generate_response(self, user_input: str, conversation_history: List[ChatMessage]) -> str:
        """Generate AI response using Mistral API"""
        try:
            messages = [{"role": "system", "content": self.system_prompt}]
            # Add last 20 messages from history
            messages.extend([{"role": msg.role, "content": msg.content} for msg in conversation_history[-20:]])
            messages.append({"role": "user", "content": user_input})

            headers = {
                "Authorization": f"Bearer {self.mistral_api_key}",
                "Content-Type": "application/json",
            }
            data = {
                "model": "mistral-large-latest", # Using the latest model
                "messages": messages,
                "max_tokens": 500,
                "temperature": 0.7,
            }
            response = requests.post(
                "https://api.mistral.ai/v1/chat/completions",
                headers=headers,
                json=data,
                timeout=30
            )
            response.raise_for_status() # Raise an exception for bad status codes
            result = response.json()
            return result["choices"][0]["message"]["content"]
        except requests.exceptions.RequestException as e:
            return f"API Error: {str(e)}. Please try again."
        except Exception as e:
            return f"An error occurred while generating response: {str(e)}"

# Initialize chatbot
try:
    chatbot = EnglishChatBot()
except ValueError as e:
    print(f"Configuration Error: {str(e)}")
    chatbot = None


@app.get("/")
def home():
    return {"message": "Hello from FastAPI"}

@app.get("/")
async def root():
    return {"message": "English Conversation Chatbot API", "status": "running"}

@app.post("/chat/audio")
async def chat_with_audio(audio: UploadFile = File(...), conversation_history: str = "[]"):
    """Handle audio-based chat requests"""
    if not chatbot:
        raise HTTPException(status_code=503, detail="Chatbot not properly configured")

    try:
        history_list = json.loads(conversation_history)
        history = [ChatMessage(**msg) for msg in history_list]

        transcription_result = await chatbot.process_audio_file(audio)
        if not transcription_result["success"]:
            return {"transcription": "", "response": "", "conversation_history": history_list, "error": transcription_result["error"]}

        user_text = transcription_result["text"]
        ai_response = await chatbot.generate_response(user_text, history)

        updated_history = history + [
            ChatMessage(role="user", content=user_text),
            ChatMessage(role="assistant", content=ai_response)
        ]

        return {
            "transcription": user_text,
            "response": ai_response,
            "conversation_history": [msg.model_dump() for msg in updated_history], # Use .model_dump() for Pydantic v2
            "error": None
        }
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid conversation history format")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error processing audio chat: {str(e)}")

@app.websocket("/ws/chat")
async def websocket_chat_endpoint(websocket: WebSocket):
    await websocket.accept()
    if not chatbot:
        await websocket.send_json({"type": "error", "message": "Chatbot not properly configured"})
        await websocket.close()
        return

    conversation_history = []
    try:
        while True:
            data = await websocket.receive_text()
            message_data = json.loads(data)

            if message_data["type"] == "text_message":
                user_input = message_data["content"]
                
                # Get response from the chatbot
                ai_response = await chatbot.generate_response(user_input, [ChatMessage(**msg) for msg in conversation_history])

                # Update history
                conversation_history.append({"role": "user", "content": user_input})
                conversation_history.append({"role": "assistant", "content": ai_response})

                # Send response back to client
                await websocket.send_json({
                    "type": "chat_response",
                    "user_message": user_input,
                    "ai_response": ai_response,
                    "conversation_history": conversation_history
                })
            elif message_data["type"] == "clear_history":
                conversation_history = []
                await websocket.send_json({"type": "history_cleared", "message": "Conversation history cleared"})

    except WebSocketDisconnect:
        print("WebSocket client disconnected")
    except Exception as e:
        print(f"WebSocket error: {str(e)}")
        try:
            await websocket.send_json({"type": "error", "message": f"An error occurred: {str(e)}"})
        except Exception:
            pass # Client might already be disconnected
    finally:
        if not websocket.client_state == 'DISCONNECTED':
             await websocket.close()

if __name__ == "__main__":
    import uvicorn
    print("Starting English Conversation Chatbot API...")
    print("Make sure to set MISTRAL_API_KEY in your .env file")
    uvicorn.run(app, host="0.0.0.0", port=8000)