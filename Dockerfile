FROM python:3.12-slim

WORKDIR /demo
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/demo

RUN apt-get update \
    && apt-get install -y --no-install-recommends bash ca-certificates procps redis-server redis-tools \
    && rm -rf /var/lib/apt/lists/*

COPY . /demo
