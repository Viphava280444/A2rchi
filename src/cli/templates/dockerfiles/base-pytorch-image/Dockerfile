# syntax=docker/dockerfile:1
FROM docker.io/pytorch/pytorch:2.6.0-cuda12.4-cudnn9-devel
RUN mkdir -p /root/archi
WORKDIR /root/archi

COPY requirements.txt requirements.txt
COPY LICENSE LICENSE

RUN pip install --upgrade pip && pip install -r requirements.txt
