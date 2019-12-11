FROM python:3.7.2

ADD . /app
WORKDIR /app

RUN python -m pip --no-cache-dir install --user -r requirements.txt \
    && python -m pip --no-cache-dir install --user -r tests/requirements.txt

CMD export PYTHONPATH=.:vendor \
    && python -m unittest discover tests
