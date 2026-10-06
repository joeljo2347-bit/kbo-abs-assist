FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY abs_assist abs_assist
ENV ABS_DB=/app/data/abs.db
EXPOSE 8000
CMD ["uvicorn", "abs_assist.api:app", "--host", "0.0.0.0", "--port", "8000"]
