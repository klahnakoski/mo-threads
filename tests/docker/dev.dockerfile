FROM python:3.7.2

WORKDIR /app
RUN mkdir tests
COPY requirements.txt /app
COPY tests/requirements.txt /app/tests
RUN python -m pip install -r requirements.txt \
    && python -m pip install -r tests/requirements.txt

ADD . /app
CMD export PYTHONPATH=.:vendor \
    && python -m unittest -k tests.test_python_process
