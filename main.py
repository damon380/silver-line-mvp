"""
Silver-Line MVP  –  free tier, no TTS package
FastAPI + Vosk offline STT + LangGraph PHQ-9
Twilio Media Streams + <Say> for speech
"""
import os, json, base64, asyncio, logging
from typing import List
import numpy as np
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import PlainTextResponse
from twilio.twiml.voice_response import VoiceResponse, Stream
import vosk
from langgraph.graph import StateGraph
from langchain_core.messages import AIMessage, HumanMessage
from datetime import datetime

# ---------- 0.  logger ----------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("silver-line")

# ---------- 1.  global STT ----------
MODEL_DIR = "vosk-model-small-en-0.15"

# sanity check
if not os.path.isdir(MODEL_DIR):
    raise RuntimeError(f"Vosk model folder '{MODEL_DIR}' not found – did you forget to git-add it?")

vosk_model = vosk.Model(MODEL_DIR)
vosk_rec = vosk.KaldiRecognizer(vosk_model, 16000)

# ---------- 2.  PHQ-9 questions ----------
QUESTIONS = [
    "Little interest or pleasure in doing things?",
    "Feeling down, depressed, or hopeless?",
    "Trouble falling or staying asleep, or sleeping too much?",
    "Feeling tired or having little energy?",
    "Poor appetite or overeating?",
    "Feeling bad about yourself or that you are a failure or have let yourself or your family down?",
    "Trouble concentrating on things, such as reading the newspaper or watching television?",
    "Moving or speaking so slowly that other people could have noticed? Or the opposite — being so fidgety or restless that you have been moving around a lot more than usual?",
    "Thoughts that you would be better off dead, or of hurting yourself?"
]
MAX_QUESTION = len(QUESTIONS)

# ---------- 3.  state ----------
class State(dict):
    q_idx: int = 0
    answers: List[int]

# ---------- 4.  LangGraph ----------
def ask_question(state: State):
    q = QUESTIONS[state["q_idx"]]
    return {"reply_text": q}

def collect_answer(state: State, human_msg: str):
    mapping = {"kosong": 0, "satu": 1, "dua": 2, "tiga": 3, "0": 0, "1": 1, "2": 2, "3": 3}
    score = mapping.get(human_msg.lower(), 0)
    state["answers"].append(score)
    state["q_idx"] += 1
    return state

def should_continue(state: State):
    return "ask" if state["q_idx"] < MAX_QUESTION else "end"

def final_summary(state: State):
    total = sum(state["answers"])
    if total >= 10:
        msg = "Thank you. I will connect you to our nurse now."
    else:
        msg = "Thank you, have a nice day."
    return {"reply_text": msg, "total": total}

workflow = StateGraph(State)
workflow.add_node("ask", ask_question)
workflow.add_node("end", final_summary)
workflow.add_conditional_edges("ask", should_continue, {"ask": "ask", "end": "end"})
workflow.set_entry_point("ask")
graph = workflow.compile()

# ---------- 5.  FastAPI ----------
app = FastAPI(title="Silver-Line MVP", version="0.2.0")

# ---- 5a.  Twilio outbound webhook ----
@app.post("/voice", response_class=PlainTextResponse)
def voice():
    resp = VoiceResponse()
    resp.say("Hi, this is Silver-Line. May I ask you a few questions?", voice="alice", language="en-US")
    start = Start()
    start.stream(url=f"wss://{os.getenv('RENDER_EXTERNAL_URL', 'localhost')}/stream")
    resp.append(start)
    return str(resp)

# ---- 5b.  Media Stream websocket ----
@app.websocket("/stream")
async def stream_ws(websocket: WebSocket):
    await websocket.accept()
    state = State(q_idx=0, answers=[])
    # push first question
    for chunk in graph.stream(state):
        if "reply_text" in chunk:
            await websocket.send_json({
                "event": "say",
                "say": {"text": chunk["reply_text"], "voice": "alice", "language": "en-US"}
            })

    # listen for answers
    try:
        while True:
            msg = await websocket.receive_json()
            if msg["event"] == "media":
                audio_b64 = msg["media"]["payload"]
                audio_bytes = base64.b64decode(audio_b64)
                if vosk_rec.AcceptWaveform(audio_bytes):
                    result = json.loads(vosk_rec.Result())
                    if result.get("text"):
                        state = collect_answer(state, result["text"])
                        for chunk in graph.stream(state):
                            if "reply_text" in chunk:
                                await websocket.send_json({
                                    "event": "say",
                                    "say": {"text": chunk["reply_text"], "voice": "alice", "language": "en-US"}
                                })
                # finished?
                if state["q_idx"] >= MAX_QUESTION:
                    for chunk in graph.stream(state):
                        if "total" in chunk:
                            logger.info("PHQ-9 total = %s", chunk["total"])
                            # TODO: POST to FHIR here
                    break
    except WebSocketDisconnect:
        logger.info("Client disconnected")
