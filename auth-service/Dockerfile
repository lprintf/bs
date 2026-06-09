FROM python:3.12-alpine

RUN pip install --no-cache-dir cryptography

WORKDIR /app
COPY server.py /app/server.py
COPY generate_demo_keys.py /app/generate_demo_keys.py
RUN python /app/generate_demo_keys.py /app/keys demo-ed25519-1 demo-ed25519-2

EXPOSE 8000
CMD ["python", "/app/server.py"]
