FROM python:3.11-slim
WORKDIR /app

COPY . .
RUN pip install python-telegram-bot==21.0

CMD ["python3", "main.py"]
