ARG BASE_IMAGE=python:3.12-slim
FROM ${BASE_IMAGE}
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY hoomy/ ./hoomy/
RUN mkdir /data && chmod 700 /data
EXPOSE 8765
CMD ["python", "-m", "hoomy.server", "--host", "0.0.0.0", "--port", "8765", "--data-dir", "/data"]
