FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir fastapi uvicorn qdrant-client requests
COPY ingestion/embeddings.py ingestion/store.py /app/ingestion/
COPY rag/ /app/rag/
WORKDIR /app/rag
EXPOSE 8000
CMD ["uvicorn", "rag_service:app", "--host", "0.0.0.0", "--port", "8000"]