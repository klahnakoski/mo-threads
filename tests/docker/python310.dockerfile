FROM ubuntu:22.04

RUN apt update && apt install -y python3.10 python3.10-venv python3.10-dev
RUN update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.10 1
RUN python3 --version
RUN mkdir app
WORKDIR /app
RUN python3 -m venv .venv
RUN . .venv/bin/activate
RUN .venv/bin/pip install --upgrade pip

# PRE LOAD THE REQUIREMENTS FOR FASTER RUNS
COPY packaging/requirements.txt /app/packaging/requirements.txt
COPY tests/requirements.txt /app/tests/requirements.txt
RUN .venv/bin/pip install -r tests/requirements.txt
RUN .venv/bin/pip install -r packaging/requirements.txt

# ACTUAL SETUP
COPY mo_threads/ /app/mo_threads/
COPY tests/ /app/tests/

RUN .venv/bin/pip install -r tests/requirements.txt
RUN .venv/bin/pip install -r packaging/requirements.txt

ENV PYTHONPATH=.
# RUN .venv/bin/python tests/smoke_test.py
# RUN .venv/bin/python -m unittest discover tests -v

