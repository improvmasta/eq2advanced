FROM python:3.11-slim
WORKDIR /app
COPY public/ ./public/
EXPOSE 8450
CMD ["python", "-m", "http.server", "8450", "--bind", "0.0.0.0", "--directory", "public"]
