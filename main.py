"""
Silver-Line MVP  –  free tier
FastAPI + Vosk STT + Mozilla TTS + LangGraph PHQ-9
Twilio Media Streams → WebSocket
"""
import os, json, base64, asyncio, logging
from typing import List
import numpy as np
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import PlainTextResponse
from twilio.twiml.voice_response import VoiceResponse, Start, Stream
import vosk, TTS                          # offline models
from langgraph.graph import StateGraph
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool
from datetime import datetime

# ---------- 0.  logger ----------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("silver-line")

# ---------- 1.  global STT ----------
MODEL_URL = "https://alphacephei.com/vosk/models/vosk-model-small-en-0.15.zip"
MODEL_DIR = "vosk-model-small-en-0.15"
if not os.path.exists(MODEL_DIR):
    # Render buildpack will download once (or pre-install via Dockerfile)
    os.system(f"wget -q {MODEL_URL} && unzip -q vosk-model-small-en-0.15.zip && rm vosk-model-small-en-0.15.zip")
vosk_model = vosk.Model(MODEL_DIR)
vosk_rec = vosk.KaldiRecognizer(vosk_model, 16000)

# ---------- 2.  global TTS (Mozilla) ----------
tts_model = TTS.TTS(model_name="tts_models/en/ljspeech/tacotron2-DDC", progress_bar=False, gpu=False)

# ---------- 3.  LangGraph – 9 PHQ-9 questions ----------
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

# ---------- 4.  state ----------
class State(dict):
    q_idx: int = 0
    answers: List[int]

# ---------- 5.  LangGraph ----------
def ask_question(state: State):
    q = QUESTIONS[state["q_idx"]]
    audio_bytes = tts_model.tts(text=q)
    return {"reply_audio": audio_bytes, "reply_text": q}

def collect_answer(state: State, human_msg: str):
    # accept 0-3 or "kosong","satu","dua","tiga"
    mapping = {"kosong":0,"satu":1,"dua":2,"tiga":3,"0":0,"1":1,"2":2,"3":3}
    score = mapping.get(human_msg.lower(), 0)
    state["answers"].append(score)
    state["q_idx"] += 1
    return state

def should_continue(state: State):
    return "ask" if state["q_idx"] < MAX_QUESTION else "end"

def total_score(state: State):
    total = sum(state["answers"])
    if total >= 10:
        msg = "Thank you. I will connect you to our nurse now."
    else:
        msg = "Thank you, have a nice day."
    audio_bytes = tts_model.tts(text=msg)
    return {"reply_audio": audio_bytes, "reply_text": msg, "total": total}

workflow = StateGraph(State)
workflow.add_node("ask", ask_question)
workflow.add_node("end", total_score)
workflow.add_conditional_edges("ask", should_continue, {"ask": "ask", "end": "end"})
workflow.set_entry_point("ask")
graph = workflow.compile()

# ---------- 6.  FastAPI ----------
app = FastAPI(title="Silver-Line MVP", version="0.1.0")

# ---- 6a.  Twilio outbound webhook ----
@app.post("/voice", response_class=PlainTextResponse)
def voice():
    resp = VoiceResponse()
    # start streaming audio to our websocket
    resp.say("Hi, this is Silver-Line. May I ask you a few questions?", voice="alice", language="en-US")
    start = Start()
    start.stream(url=f"wss://{os.getenv('RENDER_EXTERNAL_URL', 'localhost')}/stream")
    resp.append(start)
    return str(resp)

# ---- 6b.  Media Stream websocket ----
@app.websocket("/stream")
async def stream_ws(websocket: WebSocket):
    await websocket.accept()
    state = State(q_idx=0, answers=[])
    # graph first invocation
    for chunk in graph.stream(state):
        if "reply_audio" in chunk:
            pcm = chunk["reply_audio"]
            # Twilio expects 16-bit 8 kHz mu-law
            pcm_8k = np.array(pcm, dtype=np.float32)
            pcm_8k = np.clip(pcm_8k, -1, 1)
            pcm_16 = (pcm_8k * 32767).astype(np.int16)
            # simple downsampling 22 kHz → 8 kHz for phone
            pcm_8k = pcm_16[::3]
            # send as raw mulaw (base64)
            mu = pcm_8k.tobytes()
            payload = base64.b64encode(mu).decode()
            await websocket.send_json({
                "event": "media",
                "streamSid": "SILVER",
                "media": {"payload": payload}
            })

    # now listen for answers
    try:
        while True:
            msg = await websocket.receive_json()
            if msg["event"] == "media":
                audio_b64 = msg["media"]["payload"]
                audio_bytes = base64.b64decode(audio_b64)
                # feed 8 kHz mu-law to Vosk
                if vosk_rec.AcceptWaveform(audio_bytes):
                    result = json.loads(vosk_rec.Result())
                    if result.get("text"):
                        # push into graph
                        state = collect_answer(state, result["text"])
                        for chunk in graph.stream(state):
                            if "reply_audio" in chunk:
                                pcm = chunk["reply_audio"]
                                pcm_8k = (np.array(pcm, dtype=np.float32)[::3] * 32767).astype(np.int16)
                                payload = base64.b64encode(pcm_8k.tobytes()).decode()
                                await websocket.send_json({"event": "media", "media": {"payload": payload}})
                # final
                if state["q_idx"] >= MAX_QUESTION:
                    for chunk in graph.stream(state):
                        if "total" in chunk:
                            # could POST to FHIR here
                            logger.info("PHQ-9 total = %s", chunk["total"])
                    break
    except WebSocketDisconnect:
        logger.info("Client disconnected")
