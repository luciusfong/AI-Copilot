import json
import re
import sys
import time
import uuid
from pathlib import Path

import requests
from fastapi import Body, FastAPI
from fastapi.responses import StreamingResponse
from qdrant_client.models import FieldCondition, Filter, MatchValue

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ingestion"))
from embeddings import OLLAMA_URL, embed_query  # noqa: E402
from store import client  # noqa: E402

MODEL_ID = "engineering-assistant"
CHAT_MODEL = "qwen3:4b"
NUM_CTX = 8192          # raise to 16384 if your RAM/VRAM allows
CONTEXT_BUDGET = 14000  # characters of retrieved context
MAX_BLOCK = 2500
CODE, DOCS = "engineering_code", "engineering_docs"

# PascalCase / camelCase identifiers in the question, e.g. CreateLot, OrderProcessor
IDENT = re.compile(r"\b(?:[A-Z][a-z0-9]+(?:[A-Z][A-Za-z0-9]*)+|[a-z]+[A-Z][A-Za-z0-9]*)\b")

SYSTEM = """You are an engineering assistant for the user's private codebase and documentation.
Answer using ONLY the context below and cite sources inline as [n].
- For "where is X used" or "what calls X", rely on the CALLERS and USED BY sections; they come from an exact index.
- Explain workflows step by step, following the calls between methods.
- If the context does not contain the answer, say what is missing instead of guessing.
- Never invent classes, methods or files."""

app = FastAPI()


# ---------- retrieval ----------
def eq(key, val):
    return FieldCondition(key=key, match=MatchValue(value=val))


def scroll(flt, limit=30):
    pts, _ = client.scroll(collection_name=CODE, scroll_filter=flt, limit=limit, with_payload=True)
    return sorted((p.payload for p in pts), key=lambda p: (p["path"], p["start_line"]))


def code_label(p):
    return f"{p['repository']}/{p['path']}:{p['start_line']}-{p['end_line']}"


def code_text(p):
    name = ".".join(x for x in (p["namespace"], p["class"], p["method"]) if x)
    return f"{p['symbol_type']} {name}\n{p['content']}"


def retrieve(question):
    notes, defs, semantic, seen = [], [], [], set()

    def add(bucket, label, text):
        if label is not None:
            if label in seen:
                return
            seen.add(label)
        bucket.append({"label": label, "text": text})

    for name in list(dict.fromkeys(IDENT.findall(question)))[:4]:
        # exact call-graph lookups (small, so they go first)
        callers = scroll(Filter(must=[eq("called_names", name)]), limit=25)
        if callers:
            lines = [f"- {c['class']}.{c['method']} ({c['path']}:{c['start_line']}) via "
                     + ", ".join(x for x in c["calls"] if name in x)[:90] for c in callers]
            add(notes, None, f"CALLERS of {name} (exact index):\n" + "\n".join(lines))
        users = scroll(Filter(should=[eq("dependencies", name), eq("created_types", name),
                                      eq("base_types", name)]), limit=25)
        if users:
            lines = [f"- {u['class']}.{u['method'] or ''} ({u['path']}:{u['start_line']}) [{u['symbol_type']}]"
                     for u in users]
            add(notes, None, f"USED BY / DEPENDS ON {name} (exact index):\n" + "\n".join(lines))

        # definitions: the class summary plus a few members, and any method with that name
        recs = scroll(Filter(must=[eq("class", name)]), limit=60)
        for p in [r for r in recs if r["method"] is None] + [r for r in recs if r["method"]][:6]:
            add(defs, code_label(p), code_text(p))
        for p in scroll(Filter(must=[eq("method", name)]), limit=4):
            add(defs, code_label(p), code_text(p))

    # semantic search over code and docs
    qv = embed_query(question)
    for col, k in ((CODE, 5), (DOCS, 4)):
        res = client.query_points(collection_name=col, query=qv, limit=k, with_payload=True)
        for r in res.points:
            p = r.payload
            if col == CODE:
                add(semantic, code_label(p), code_text(p))
            else:
                label = f"{p['path']} > {p['heading']}" if p["heading"] else p["path"]
                add(semantic, label, p["content"])

    return notes + defs + semantic


def build_context(entries):
    parts, sources, used = [], [], 0
    for e in entries:
        text = e["text"][:MAX_BLOCK]
        if used + len(text) > CONTEXT_BUDGET:
            continue
        used += len(text)
        if e["label"]:
            sources.append(e["label"])
            parts.append(f"[{len(sources)}] {e['label']}\n{text}")
        else:
            parts.append(text)
    return "\n\n---\n\n".join(parts), sources


# ---------- chat plumbing ----------
def text_of(m):
    c = m.get("content", "")
    if isinstance(c, list):
        return " ".join(x.get("text", "") for x in c if isinstance(x, dict))
    return c or ""


def prepare(body):
    msgs = [{"role": m["role"], "content": text_of(m)} for m in body["messages"]]
    users = [m["content"] for m in msgs if m["role"] == "user"]
    q = users[-1] if users else ""
    if q.startswith("### Task:"):      # OpenWebUI housekeeping (titles, tags): skip retrieval
        return msgs, []
    if len(q) < 60 and len(users) > 1:  # short follow-up: borrow the previous question
        q = users[-2] + "\n" + q
    ctx, sources = build_context(retrieve(q))
    print(f"[rag] q={q[:80]!r} -> {len(sources)} sources")
    system = SYSTEM + "\n\nCONTEXT:\n" + (ctx or "(nothing relevant was found in the index)")
    history = [m for m in msgs if m["role"] != "system"][-6:]
    return [{"role": "system", "content": system}] + history, sources


def ollama_stream(messages):
    r = requests.post(
        f"{OLLAMA_URL}/api/chat",
        json={"model": CHAT_MODEL, "messages": messages, "stream": True, "think": False,
              "options": {"num_ctx": NUM_CTX, "temperature": 0.2}},
        stream=True, timeout=600)
    r.raise_for_status()
    for line in r.iter_lines():
        if not line:
            continue
        d = json.loads(line)
        piece = d.get("message", {}).get("content", "")
        if piece:
            yield piece
        if d.get("done"):
            break


def footer(answer, sources):
    if not sources:
        return ""
    cited = sorted({int(n) for n in re.findall(r"\[(\d+)\]", answer) if 0 < int(n) <= len(sources)})
    shown = cited or list(range(1, min(len(sources), 3) + 1))
    return "\n\n---\n**Sources**\n" + "\n".join(f"- [{n}] `{sources[n - 1]}`" for n in shown)


def sse(cid, delta, finish=None):
    chunk = {"id": cid, "object": "chat.completion.chunk", "created": int(time.time()),
             "model": MODEL_ID, "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
    return f"data: {json.dumps(chunk)}\n\n"


@app.get("/v1/models")
def models():
    return {"object": "list",
            "data": [{"id": MODEL_ID, "object": "model", "created": 0, "owned_by": "local"}]}


@app.post("/v1/chat/completions")
def chat(body: dict = Body(...)):
    messages, sources = prepare(body)
    cid = f"chatcmpl-{uuid.uuid4().hex[:12]}"

    if not body.get("stream"):
        answer = "".join(ollama_stream(messages))
        answer += footer(answer, sources)
        return {"id": cid, "object": "chat.completion", "created": int(time.time()),
                "model": MODEL_ID,
                "choices": [{"index": 0, "message": {"role": "assistant", "content": answer},
                             "finish_reason": "stop"}]}

    def gen():
        yield sse(cid, {"role": "assistant", "content": ""})
        answer = ""
        for piece in ollama_stream(messages):
            answer += piece
            yield sse(cid, {"content": piece})
        tail = footer(answer, sources)
        if tail:
            yield sse(cid, {"content": tail})
        yield sse(cid, {}, "stop")
        yield "data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")