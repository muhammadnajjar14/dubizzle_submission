from fastapi import FastAPI
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
from app.agent import chat_with_agent

app = FastAPI(title="dubizzle Car Assistant API")

# Allow the local Streamlit client to call the API.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

class ChatRequest(BaseModel):
    session_id: str
    user_id: str
    message: str

class ChatResponse(BaseModel):
    response: str

# Delegate the conversation to the agent layer.
@app.post("/chat", response_model=ChatResponse)
def chat_endpoint(req: ChatRequest):
    return ChatResponse(
        response=chat_with_agent(
            req.session_id,
            req.user_id,
            req.message,
        )
    )

@app.get("/health")
def health_check():
    return {"status": "ok", "message": "FastAPI is running"}
